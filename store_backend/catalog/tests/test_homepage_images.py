# ruff: noqa: PLR2004, F811
"""Images of Homepage sections: uploaded in the Vitrin, never a Slot (#168)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.catalog.models import Banner
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.services import banners
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

HOME = "/api/admin/homepage"
IMAGES = f"{HOME}/images"
STORE = "/api/store/homepage"


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


def upload(api, **fields):
    response = post_file(api, IMAGES, make_file(), **fields)
    assert response.status_code == HTTPStatus.CREATED, response.content
    made = response.json()
    banners.process_image(made["id"], "image")
    return made


def layout(api, sections):
    response = api.put(HOME, {"sections": sections})
    assert response.status_code == HTTPStatus.OK, response.content


def banner_section(image_id, section_id="own-banner"):
    return {
        "id": section_id,
        "type": "banner",
        "settings": {"banner_id": image_id},
    }


def own_slider(*ids):
    return {
        "id": "own-slider",
        "type": "slider",
        "settings": {"source": "own", "banner_ids": list(ids)},
    }


def storefront_ids():
    return [s["id"] for s in Client().get(STORE).json()["sections"]]


def test_an_upload_makes_a_layout_image_with_its_alt_text_and_link(boss):
    response = post_file(
        boss,
        IMAGES,
        make_file(),
        alt_text="Yaz koleksiyonu",
        link="/ara?sort=newest",
    )

    assert response.status_code == HTTPStatus.CREATED, response.content
    made = response.json()
    assert made["alt_text"] == "Yaz koleksiyonu"
    assert made["link"] == "/ara?sort=newest"
    assert made["image"]["status"] == "processing"
    assert Banner.objects.get(pk=made["id"]).slot == Banner.Slot.LAYOUT


def test_a_layout_image_is_not_a_slot_banner(boss):
    made = upload(boss)

    assert boss.get("/api/admin/banners").json() == []
    assert boss.get(f"/api/admin/banners/{made['id']}").status_code == 404
    edited = boss.patch(f"/api/admin/banners/{made['id']}", {"link": "/x"})
    assert edited.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    shown = Client().get("/api/store/banners").json()
    assert all(not cards for cards in shown.values())
    assert "layout" not in shown


def test_the_banners_screen_cannot_create_a_layout_banner(boss):
    response = boss.post("/api/admin/banners", {"slot": "layout", "link": ""})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_afis_section_shows_its_uploaded_image_once_the_layout_is_live(boss):
    made = upload(boss, alt_text="Kampanya")
    layout(boss, [banner_section(made["id"])])

    card = Client().get(STORE).json()["sections"][0]

    assert card["id"] == "own-banner"
    assert card["banners"][0]["alt"] == "Kampanya"
    assert card["banners"][0]["image"]


def test_an_image_nobody_uses_is_not_public(boss):
    upload(boss)

    assert storefront_ids() == [s["id"] for s in Client().get(STORE).json()["sections"]]
    assert "layout" not in str(Client().get("/api/store/banners").json())


def test_the_own_slider_shows_the_uploaded_slides_in_order(boss):
    first, second = upload(boss, alt_text="Bir"), upload(boss, alt_text="İki")
    layout(boss, [own_slider(second["id"], first["id"])])

    section = Client().get(STORE).json()["sections"][0]

    assert [b["alt"] for b in section["banners"]] == ["İki", "Bir"]


def test_an_own_slider_needs_at_least_one_slide(boss):
    response = boss.put(HOME, {"sections": [own_slider()]})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_slide_that_is_not_ready_is_left_out_and_warned_about(boss):
    response = post_file(boss, IMAGES, make_file())  # derivatives not made yet
    made = response.json()
    layout(boss, [own_slider(made["id"])])

    assert storefront_ids() == []
    assert "own-slider" in boss.get(HOME).json()["warnings"]


def test_staff_upload_but_their_slide_waits_for_the_owner(staff, boss):
    made = upload(staff, alt_text="Personel")
    staff.put(HOME, {"sections": [own_slider(made["id"])]})

    assert "own-slider" not in storefront_ids()
    preview = boss.get(f"{HOME}/preview").json()["sections"]
    assert [s["id"] for s in preview] == ["own-slider"]

    assert boss.post("/api/admin/approvals/homepage/1/approve").status_code == 200
    assert storefront_ids() == ["own-slider"]


def test_the_alt_text_and_link_can_be_changed(boss):
    made = upload(boss, alt_text="Eski")

    response = boss.patch(
        f"{IMAGES}/{made['id']}", {"alt_text": "Yeni", "link": "/yeni"}
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert (response.json()["alt_text"], response.json()["link"]) == ("Yeni", "/yeni")


def test_only_internal_or_https_links_are_kept(boss):
    made = upload(boss)

    response = boss.patch(f"{IMAGES}/{made['id']}", {"link": "http://example.com"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_main_picture_can_be_replaced(boss):
    made = upload(boss)
    old = Banner.objects.get(pk=made["id"]).image_original

    response = post_file(
        boss, f"{IMAGES}/{made['id']}/image", make_file(colour=(0, 0, 200))
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["id"] == made["id"]
    assert Banner.objects.get(pk=made["id"]).image_original != old


def test_a_mobile_picture_can_be_added_and_removed(boss):
    made = upload(boss)

    added = post_file(boss, f"{IMAGES}/{made['id']}/mobile-image", make_file())
    assert added.status_code == HTTPStatus.OK, added.content
    assert added.json()["mobile_image"]["status"] == "processing"

    removed = boss.delete(f"{IMAGES}/{made['id']}/mobile-image")
    assert removed.status_code == HTTPStatus.OK
    assert removed.json()["mobile_image"] is None
    assert removed.json()["image"] is not None


def test_a_bad_file_is_refused_with_a_reason(boss):
    response = post_file(boss, IMAGES, make_file(size=(40, 40)))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert Banner.objects.filter(slot=Banner.Slot.LAYOUT).count() == 0


def test_the_listing_returns_images_in_the_order_asked_and_skips_other_banners(boss):
    first, second = upload(boss), upload(boss)
    slot_banner = boss.post("/api/admin/banners", {"slot": "home_hero"}).json()

    ids = [second["id"], slot_banner["id"], first["id"]]
    rows = boss.get(f"{IMAGES}?" + "&".join(f"ids={i}" for i in ids))

    assert [r["id"] for r in rows.json()] == [second["id"], first["id"]]


def test_a_shopper_cannot_touch_layout_images():
    client = Client()

    assert client.get(f"{IMAGES}?ids=1").status_code in {401, 403}
    assert client.post(IMAGES, {"file": make_file()}).status_code in {401, 403}
