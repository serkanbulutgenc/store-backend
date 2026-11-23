# ruff: noqa: PLR2004, F811
"""Banners: Staff draft, the Owner approves, the Storefront shows per slot (#112)."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.test import Client
from django.utils import timezone
from oscar.apps.catalogue.models import Category

from store_backend.catalog.models import Banner
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.services import approval_queue
from store_backend.services import banners
from store_backend.staff.models import AuditEntry
from store_backend.staff.models import PendingChange
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin/banners"
STORE = "/api/store/banners"


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


def create(client, **fields):
    body = {"slot": "announcement", "message": "Kargo bedava", **fields}
    response = client.post(ADMIN, body)
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def hero(client, **fields):
    return create(client, slot="home_hero", message="", **fields)


def with_image(boss, made, which="image"):
    url = f"{ADMIN}/{made['id']}/{'image' if which == 'image' else 'mobile-image'}"
    response = post_file(boss, url, make_file())
    assert response.status_code == HTTPStatus.OK, response.content
    banners.process_image(made["id"], which)


def go_live(boss, made):
    assert boss.post(f"{ADMIN}/{made['id']}/publish").status_code == 200


def shown(**query):
    response = Client().get(STORE, query)
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


# -- drafting and the approval rule ----------------------------------------------


def test_staff_draft_and_cannot_publish(staff, boss):
    made = create(staff)

    assert made["status"] == "draft"
    assert staff.post(f"{ADMIN}/{made['id']}/publish").status_code == 403
    assert staff.post(f"{ADMIN}/{made['id']}/unpublish").status_code == 403
    assert shown()["announcement"] == []


def test_staff_submit_and_the_owner_approves_from_the_queue(staff, boss, owner):
    made = create(staff)
    assert staff.post(f"{ADMIN}/{made['id']}/submit").json()["status"] == (
        "pending_approval"
    )
    assert shown()["announcement"] == []
    queue = boss.get("/api/admin/approvals").json()
    assert [(i["type"], i["id"]) for i in queue["items"]] == [("banner", made["id"])]

    approval_queue.approve(owner, "banner", made["id"])

    assert [b["message"] for b in shown()["announcement"]] == ["Kargo bedava"]


def test_staff_edit_of_a_live_banner_waits_for_the_owner(staff, boss, owner):
    made = create(boss)
    go_live(boss, made)

    edited = staff.patch(f"{ADMIN}/{made['id']}", {"message": "Yeni mesaj"})

    assert edited.status_code == 200
    assert edited.json()["message"] == "Kargo bedava"
    assert edited.json()["pending_change"]["payload"]["message"] == "Yeni mesaj"
    assert shown()["announcement"][0]["message"] == "Kargo bedava"
    approval_queue.approve(owner, "banner", made["id"])
    assert shown()["announcement"][0]["message"] == "Yeni mesaj"
    assert not PendingChange.objects.exists()


def test_dates_survive_a_pending_change(staff, boss, owner):
    made = create(boss)
    go_live(boss, made)
    start = (timezone.now() - timedelta(days=1)).isoformat()
    end = (timezone.now() + timedelta(days=3)).isoformat()

    staff.patch(f"{ADMIN}/{made['id']}", {"starts_at": start, "ends_at": end})
    approval_queue.approve(owner, "banner", made["id"])

    banner = Banner.objects.get(pk=made["id"])
    assert banner.starts_at
    assert banner.ends_at


def test_every_step_is_audited(staff, owner):
    made = create(staff)
    staff.post(f"{ADMIN}/{made['id']}/submit")
    approval_queue.approve(owner, "banner", made["id"])

    assert AuditEntry.objects.count() >= 3  # created, submitted, approved


# -- validation ------------------------------------------------------------------


def test_the_announcement_bar_needs_a_message(staff):
    response = staff.post(ADMIN, {"slot": "announcement", "message": " "})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "message" in response.json()["fields"]


def test_an_unknown_slot_is_refused(staff):
    response = staff.post(ADMIN, {"slot": "popup"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "slot" in response.json()["fields"]


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "http://example.com", "//evil.test", "evil.test/x"],
)
def test_only_internal_or_https_links(staff, link):
    response = staff.post(ADMIN, {"slot": "home_hero", "link": link})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "link" in response.json()["fields"]


def test_internal_and_https_links_are_kept(staff):
    assert hero(staff, link="/kategori/elbise")["link"] == "/kategori/elbise"
    assert hero(staff, link="https://example.com/x")["link"] == "https://example.com/x"


def test_the_end_must_follow_the_start(staff):
    now = timezone.now()
    response = staff.post(
        ADMIN,
        {
            "slot": "home_hero",
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "ends_at" in response.json()["fields"]


def test_a_message_and_category_belong_to_their_own_slot(staff):
    category = Category.add_root(name="Elbise")

    made = staff.post(
        ADMIN,
        {
            "slot": "home_hero",
            "message": "kalmamalı",
            "category_id": category.pk,
        },
    ).json()

    assert made["message"] == ""
    assert made["category"] is None


def test_changing_the_slot_drops_what_no_longer_applies(staff):
    made = create(staff)

    changed = staff.patch(f"{ADMIN}/{made['id']}", {"slot": "home_mid"}).json()

    assert (changed["slot"], changed["message"]) == ("home_mid", "")


def test_a_missing_category_is_refused(staff):
    response = staff.post(ADMIN, {"slot": "category_top", "category_id": 9999})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# -- images ----------------------------------------------------------------------


def test_staff_upload_images_on_a_draft_and_they_show_once_ready(staff, boss):
    made = hero(staff)

    response = post_file(
        staff,
        f"{ADMIN}/{made['id']}/image",
        make_file(),
        alt_text="Yaz",
    )
    assert response.json()["image"]["status"] == "processing"
    assert (
        post_file(staff, f"{ADMIN}/{made['id']}/mobile-image", make_file()).status_code
        == 200
    )
    go_live(boss, made)
    assert shown()["home_hero"] == []  # not ready yet

    banners.process_image(made["id"], "image")
    card = shown()["home_hero"][0]
    assert card["alt"] == "Yaz"
    assert [s["width"] for s in card["image"]] == [400, 800]
    assert card["mobile_image"] is None  # its derivatives are not made yet
    banners.process_image(made["id"], "mobile")
    assert shown()["home_hero"][0]["mobile_image"]


def test_staff_cannot_change_the_images_of_a_live_banner_but_the_owner_can(
    staff,
    boss,
):
    made = hero(boss)
    go_live(boss, made)

    assert (
        post_file(staff, f"{ADMIN}/{made['id']}/image", make_file()).status_code == 403
    )
    assert (
        post_file(boss, f"{ADMIN}/{made['id']}/image", make_file()).status_code == 200
    )
    assert boss.delete(f"{ADMIN}/{made['id']}/image").json()["image"] is None


def test_the_announcement_bar_takes_no_image(staff):
    made = create(staff)

    response = post_file(staff, f"{ADMIN}/{made['id']}/image", make_file())

    assert response.status_code == HTTPStatus.CONFLICT


def test_a_bad_image_is_refused(staff):
    made = hero(staff)

    response = post_file(
        staff,
        f"{ADMIN}/{made['id']}/image",
        make_file(size=(100, 100)),
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_removing_the_mobile_image_keeps_the_main_one(boss):
    made = hero(boss)
    with_image(boss, made)
    with_image(boss, made, "mobile")

    removed = boss.delete(f"{ADMIN}/{made['id']}/mobile-image").json()

    assert removed["mobile_image"] is None
    assert removed["image"]["status"] == "ready"


# -- the Storefront --------------------------------------------------------------


def test_only_published_banners_inside_their_dates_in_order(boss):
    now = timezone.now()
    first = create(boss, message="B", position=2)
    second = create(boss, message="A", position=1)
    soon = create(
        boss,
        message="yakında",
        starts_at=(now + timedelta(days=1)).isoformat(),
    )
    over = create(
        boss,
        message="bitti",
        starts_at=(now - timedelta(days=3)).isoformat(),
        ends_at=(now - timedelta(days=1)).isoformat(),
    )
    for made in (first, second, soon, over):
        go_live(boss, made)
    create(boss, message="taslak")

    assert [b["message"] for b in shown()["announcement"]] == ["A", "B"]


def test_an_unpublished_banner_goes_away(boss):
    made = create(boss)
    go_live(boss, made)
    boss.post(f"{ADMIN}/{made['id']}/unpublish")

    assert shown()["announcement"] == []


def test_a_picture_slot_banner_without_a_ready_image_is_left_out(boss):
    made = hero(boss)
    go_live(boss, made)

    assert shown()["home_hero"] == []


def test_slots_are_kept_apart(boss):
    for slot in ("home_hero", "home_mid"):
        made = create(boss, slot=slot, message="")
        with_image(boss, made)
        go_live(boss, made)

    body = shown()

    assert (len(body["home_hero"]), len(body["home_mid"])) == (1, 1)
    assert body["announcement"] == body["category_top"] == []


def test_the_category_slot_is_filtered_by_category(boss):
    dresses = Category.add_root(name="Elbise")
    shirts = Category.add_root(name="Gömlek")
    for category_id in (dresses.pk, shirts.pk, None):
        made = create(
            boss,
            slot="category_top",
            message="",
            category_id=category_id,
            position=0 if category_id else 5,
        )
        with_image(boss, made)
        go_live(boss, made)

    assert shown()["category_top"] == []  # no Category asked for
    on_dresses = shown(category=dresses.slug)["category_top"]
    assert len(on_dresses) == 2  # its own and the one tied to none
    assert len(shown(category=shirts.slug)["category_top"]) == 2
    other = Category.add_root(name="Pantolon")
    assert len(shown(category=other.slug)["category_top"]) == 1


def test_an_unknown_category_is_not_found():
    assert Client().get(STORE, {"category": "yok"}).status_code == 404


def test_the_admin_list_filters_by_slot_and_status(staff):
    create(staff)
    hero(staff)

    assert len(staff.get(ADMIN).json()) == 2
    assert [b["slot"] for b in staff.get(f"{ADMIN}?slot=home_hero").json()] == [
        "home_hero",
    ]
    assert staff.get(f"{ADMIN}?status=published").json() == []
    assert staff.get(f"{ADMIN}?status=nonsense").status_code == 422
