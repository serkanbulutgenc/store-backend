# ruff: noqa: PLR2004, F811
"""Campaign pages: Staff draft, the Owner publishes, the Storefront shows (#109)."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.test import Client
from django.utils import timezone

from store_backend.catalog.models import Campaign
from store_backend.catalog.models import CampaignPage
from store_backend.catalog.tests.test_campaign_storefront import (
    campaign as make_campaign,
)
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import approval_queue
from store_backend.services import campaign_pages
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.models import AuditEntry
from store_backend.staff.models import PendingChange
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

PAGES = "/api/admin/campaign-pages"
STORE = "/api/store/campaign-pages"


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def shop():
    return Shop()


def create(client, **fields):
    response = client.post(PAGES, {"title": "Yaz indirimi", **fields})
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def live(boss, **fields):
    made = create(boss, description="**Yaz** geldi", **fields)
    assert boss.post(f"{PAGES}/{made['id']}/publish").status_code == 200
    return made


def public(slug="yaz-indirimi"):
    return Client().get(f"{STORE}/{slug}")


# -- drafting and the approval rule ----------------------------------------------


def test_staff_create_a_draft_with_a_slug_from_the_title(staff):
    made = create(staff)

    assert made["status"] == "draft"
    assert made["slug"] == "yaz-indirimi"
    assert create(staff)["slug"] == "yaz-indirimi-2"


def test_staff_cannot_publish_or_unpublish_or_link(staff, boss):
    made = create(staff)

    assert staff.post(f"{PAGES}/{made['id']}/publish").status_code == 403
    assert staff.post(f"{PAGES}/{made['id']}/unpublish").status_code == 403
    assert (
        staff.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": None}).status_code
        == 403
    )
    assert public().status_code == HTTPStatus.NOT_FOUND


def test_staff_submit_and_the_owner_approves_from_the_queue(staff, boss, owner):
    made = create(staff)
    submitted = staff.post(f"{PAGES}/{made['id']}/submit")
    assert submitted.json()["status"] == "pending_approval"
    assert public().status_code == HTTPStatus.NOT_FOUND
    queue = boss.get("/api/admin/approvals").json()
    assert [(i["type"], i["id"]) for i in queue["items"]] == [
        ("campaign_page", made["id"]),
    ]

    approval_queue.approve(owner, "campaign_page", made["id"])

    assert public().status_code == HTTPStatus.OK


def test_owner_rejects_with_a_reason_staff_can_read(staff, boss):
    made = create(staff)
    staff.post(f"{PAGES}/{made['id']}/submit")

    boss.post(
        f"/api/admin/approvals/campaign_page/{made['id']}/reject",
        {"reason": "Görsel yok"},
    )

    back = staff.get(f"{PAGES}/{made['id']}").json()
    assert (back["status"], back["rejection_reason"]) == ("draft", "Görsel yok")


def test_staff_edit_of_a_live_page_waits_for_the_owner(staff, boss, owner):
    made = live(boss)

    edited = staff.patch(f"{PAGES}/{made['id']}", {"title": "Yeni başlık"})

    assert edited.status_code == 200
    assert edited.json()["title"] == "Yaz indirimi"  # the live page is untouched
    assert edited.json()["pending_change"]["payload"]["title"] == "Yeni başlık"
    assert public().json()["title"] == "Yaz indirimi"
    approval_queue.approve(owner, "campaign_page", made["id"])
    assert public().json()["title"] == "Yeni başlık"
    assert not PendingChange.objects.exists()


def test_a_pending_change_survives_dates_round_trip(staff, boss, owner):
    made = live(boss)
    start = (timezone.now() - timedelta(days=1)).isoformat()
    end = (timezone.now() + timedelta(days=3)).isoformat()

    staff.patch(f"{PAGES}/{made['id']}", {"starts_at": start, "ends_at": end})
    approval_queue.approve(owner, "campaign_page", made["id"])

    page = CampaignPage.objects.get(pk=made["id"])
    assert page.starts_at is not None
    assert page.ends_at is not None
    assert page.ends_at > page.starts_at


@pytest.mark.parametrize(
    ("fields", "name"),
    [
        ({"title": "  "}, "title"),
        ({"title": "x" * 201}, "title"),
        ({"description": "x" * 20_001}, "description"),
        (
            {"starts_at": "2030-01-02T00:00:00Z", "ends_at": "2030-01-01T00:00:00Z"},
            "ends_at",
        ),
    ],
)
def test_invalid_content_is_refused(staff, fields, name):
    response = staff.post(PAGES, {"title": "Sayfa", **fields})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert name in response.json()["fields"]
    assert not CampaignPage.objects.exists()


def test_slug_must_be_unique(staff):
    create(staff)

    other = staff.post(PAGES, {"title": "Başka", "slug": "yaz-indirimi"})

    assert other.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "slug" in other.json()["fields"]


def test_drafting_and_submitting_are_audited(staff):
    made = create(staff)
    staff.post(f"{PAGES}/{made['id']}/submit")

    actions = set(AuditEntry.objects.values_list("action", flat=True))

    assert {"approval_edited", "approval_submitted"} <= actions


# -- the discount Campaign link ---------------------------------------------------


def test_only_the_owner_links_and_unlinks_and_the_campaign_is_untouched(boss, shop):
    product = shop.product("Elbise", [(shop.black, "M", 3)], price="100.00")
    discount = make_campaign(20, product)
    before = (discount.name, discount.percentage, discount.is_active)
    made = create(boss)

    linked = boss.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": discount.pk})
    assert linked.json()["campaign"]["id"] == discount.pk
    unlinked = boss.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": None})
    assert unlinked.json()["campaign"] is None

    discount.refresh_from_db()
    assert (discount.name, discount.percentage, discount.is_active) == before


def test_linking_an_unknown_campaign_is_refused(boss):
    made = create(boss)

    response = boss.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": 999999})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_staff_cannot_smuggle_a_campaign_through_edit(staff):
    made = create(staff)
    discount = Campaign.objects.create(name="x", percentage=10)

    staff.patch(f"{PAGES}/{made['id']}", {"campaign_id": discount.pk})

    assert CampaignPage.objects.get(pk=made["id"]).campaign_id is None


# -- the Storefront ---------------------------------------------------------------


def test_public_list_has_published_unended_pages_newest_first(boss):
    first = live(boss, title="Birinci")
    live(boss, title="İkinci")
    create(boss, title="Taslak")
    old = live(boss, title="Bitti")
    CampaignPage.objects.filter(pk=old["id"]).update(
        starts_at=timezone.now() - timedelta(days=9),
        ends_at=timezone.now() - timedelta(days=1),
    )
    CampaignPage.objects.filter(pk=first["id"]).update(
        published_at=timezone.now() - timedelta(days=2),
    )

    rows = Client().get(STORE).json()

    assert [r["title"] for r in rows] == ["İkinci", "Birinci"]


def test_unpublished_draft_and_pending_pages_are_404(staff, boss):
    draft = create(staff, title="Taslak")
    pending = create(staff, title="Bekleyen")
    staff.post(f"{PAGES}/{pending['id']}/submit")
    off = live(boss, title="Kapalı")
    boss.post(f"{PAGES}/{off['id']}/unpublish")

    for slug in (draft["slug"], pending["slug"], off["slug"]):
        assert public(slug).status_code == HTTPStatus.NOT_FOUND
    assert Client().get(STORE).json() == []


def test_an_ended_page_is_shown_with_the_ended_flag(boss):
    made = live(boss)
    CampaignPage.objects.filter(pk=made["id"]).update(
        starts_at=timezone.now() - timedelta(days=9),
        ends_at=timezone.now() - timedelta(days=1),
    )

    page = public().json()

    assert page["ended"] is True
    assert page["description"] == "**Yaz** geldi"


def test_a_page_that_has_not_started_is_not_public(boss):
    made = live(boss)
    CampaignPage.objects.filter(pk=made["id"]).update(
        starts_at=timezone.now() + timedelta(days=1),
    )

    assert public().status_code == HTTPStatus.NOT_FOUND
    assert Client().get(STORE).json() == []


def test_a_linked_page_lists_discounted_products_and_the_campaign_dates(boss, shop):
    product = shop.product("Elbise", [(shop.black, "M", 3)], price="100.00")
    shop.product("Başka", [(shop.black, "M", 3)], price="50.00")
    ends = timezone.now() + timedelta(days=5)
    discount = make_campaign(20, product, ends_at=ends)
    made = live(boss)
    boss.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": discount.pk})

    page = public().json()

    assert page["ended"] is False
    assert page["campaign"]["active"] is True
    assert page["campaign"]["ends_at"] is not None
    assert [i["title"] for i in page["items"]] == ["Elbise"]
    assert page["items"][0]["price"] == "80.00"


def test_a_linked_campaign_that_is_off_lists_no_products_but_keeps_its_dates(
    boss,
    shop,
):
    product = shop.product("Elbise", [(shop.black, "M", 3)], price="100.00")
    discount = make_campaign(20, product, is_active=False)
    made = live(boss)
    boss.put(f"{PAGES}/{made['id']}/campaign", {"campaign_id": discount.pk})

    page = public().json()

    assert page["campaign"]["active"] is False
    assert page["items"] == []


def test_an_unlinked_page_is_purely_editorial(boss):
    live(boss)

    page = public().json()

    assert page["campaign"] is None
    assert page["items"] == []


# -- cover image ------------------------------------------------------------------


def image_url(made):
    return f"{PAGES}/{made['id']}/image"


def test_staff_upload_a_cover_on_a_draft_and_it_shows_when_ready(staff, boss):
    made = create(staff)

    response = post_file(staff, image_url(made), make_file(), alt_text="Yaz")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["image"]["status"] == "processing"
    campaign_pages.process_image(made["id"])
    boss.post(f"{PAGES}/{made['id']}/publish")
    image = public().json()["image"]
    assert image["alt"] == "Yaz"
    assert [s["width"] for s in image["sizes"]] == [
        400,
        800,
    ]  # a 1200 px original has no 1600 size


def test_staff_cannot_change_the_cover_of_a_live_page_but_the_owner_can(staff, boss):
    made = live(boss)

    assert post_file(staff, image_url(made), make_file()).status_code == 403
    assert post_file(boss, image_url(made), make_file()).status_code == 200
    assert (
        boss.patch(image_url(made), {"alt_text": "Yeni"}).json()["image"]["alt_text"]
        == "Yeni"
    )
    assert boss.delete(image_url(made)).json()["image"] is None


def test_a_bad_image_is_refused(staff):
    made = create(staff)

    response = post_file(staff, image_url(made), make_file(size=(100, 100)))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_status_values_match_the_approval_rule():
    assert ApprovalStatus.PENDING.value == "pending_approval"
