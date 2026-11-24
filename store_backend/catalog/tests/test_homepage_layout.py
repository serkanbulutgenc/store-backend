# ruff: noqa: PLR2004, F811
"""The Homepage layout: one document of sections, resolved for the Storefront
(GLOSSARY: Homepage layout, Homepage section; ADR 0012; #154)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import HomepageLayout
from store_backend.catalog.tests.test_banners import boss  # noqa: F401
from store_backend.catalog.tests.test_banners import create
from store_backend.catalog.tests.test_banners import go_live
from store_backend.catalog.tests.test_banners import hero
from store_backend.catalog.tests.test_banners import with_image
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import homepage
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401

pytestmark = pytest.mark.django_db

URL = "/api/store/homepage"


@pytest.fixture
def shop():
    return Shop()


def page() -> dict:
    response = Client().get(URL)
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def ids() -> list[str]:
    return [s["id"] for s in page()["sections"]]


def section(section_id: str) -> dict:
    return next(s for s in page()["sections"] if s["id"] == section_id)


def titles(section_data: dict) -> list[str]:
    return [p["title"] for p in section_data["products"]]


def save(sections: list[dict]) -> None:
    HomepageLayout.objects.update_or_create(pk=1, defaults={"sections": sections})


def test_a_shop_without_a_saved_layout_gets_todays_home_page(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])

    assert ids() == ["hero", "recently-viewed", "latest", "best-selling"]
    assert HomepageLayout.objects.get().sections == homepage.default_sections()


def test_the_default_layout_has_todays_eight_blocks_in_order():
    kinds = [(s["id"], s["type"]) for s in homepage.default_sections()]

    assert kinds == [
        ("hero", "slider"),
        ("recently-viewed", "products"),
        ("latest", "products"),
        ("mid-1", "banner"),
        ("on-sale", "products"),
        ("mid-2", "banner"),
        ("best-selling", "products"),
        ("categories", "categories"),
    ]
    assert len(homepage.validate(homepage.default_sections())) == 8


def test_recently_viewed_is_left_for_the_browser_to_fill(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])

    viewed = section("recently-viewed")

    assert viewed["client_resolved"] is True
    assert viewed["products"] is None


def test_the_product_sections_use_their_own_source(client, shop):
    old = shop.product("Eski", [(shop.black, "M", 3)])
    shop.product("Yeni", [(shop.black, "M", 3)])
    shop.product("İndirimli", [(shop.black, "M", 3)], price="80.00", old_price="100.00")

    assert titles(section("latest"))[0] == "İndirimli"
    assert titles(section("on-sale")) == ["İndirimli"]
    assert section("latest")["link"] == "/ara?sort=newest"
    assert old.title in titles(section("best-selling"))


def test_a_section_with_nothing_to_show_is_left_out(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])

    assert "on-sale" not in ids()
    assert "categories" not in ids()
    assert "mid-1" not in ids()


def test_a_hidden_section_is_not_shown_but_keeps_its_settings(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])
    sections = homepage.default_sections()
    next(s for s in sections if s["id"] == "latest")["visible"] = False
    save(sections)

    assert "latest" not in ids()
    assert HomepageLayout.objects.get().sections[2]["settings"] == {"source": "latest"}


def test_the_count_limits_a_products_section(client, shop):
    for n in range(3):
        shop.product(f"Ürün {n}", [(shop.black, "M", 3)])
    sections = homepage.default_sections()
    next(s for s in sections if s["id"] == "latest")["settings"]["count"] = 2
    save(sections)

    assert len(section("latest")["products"]) == 2


def test_the_order_of_the_saved_layout_is_the_order_shown(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])
    sections = homepage.default_sections()
    sections.reverse()
    save(sections)

    assert ids() == ["best-selling", "latest", "recently-viewed", "hero"]


def test_the_slider_shows_the_newest_products_when_nothing_is_picked(client, shop):
    shop.product("Birinci", [(shop.black, "M", 3)])
    shop.product("İkinci", [(shop.black, "M", 3)])

    slider = section("hero")

    assert titles(slider) == ["İkinci", "Birinci"]
    assert slider["banners"] is None


def test_the_slider_shows_the_ana_sayfa_collection_when_it_is_published(client, shop):
    shop.product("Yeni", [(shop.black, "M", 3)])
    picked = shop.product("Seçilmiş", [(shop.black, "M", 3)])
    collection = Collection.objects.create(
        name="Ana sayfa",
        slug="ana-sayfa",
        status=CollectionStatus.PUBLISHED,
    )
    CollectionProduct.objects.create(collection=collection, product=picked, position=1)

    assert titles(section("hero")) == ["Seçilmiş"]


def test_the_home_hero_banners_win_over_the_collection(boss, shop):
    shop.product("Yeni", [(shop.black, "M", 3)])
    made = hero(boss)
    with_image(boss, made)
    go_live(boss, made)

    slider = section("hero")

    assert len(slider["banners"]) == 1
    assert slider["products"] is None


def test_mid_page_banners_fill_the_banner_sections_in_order(boss, shop):
    shop.product("Yeni", [(shop.black, "M", 3)])
    for _ in range(2):
        made = create(boss, slot="home_mid", message="")
        with_image(boss, made)
        go_live(boss, made)

    assert ids().index("mid-1") < ids().index("mid-2")
    assert section("mid-1")["banners"][0]["id"] != section("mid-2")["banners"][0]["id"]


def test_the_picked_categories_fill_the_categories_section(client, shop):
    shop.product("Elbise", [(shop.black, "M", 3)])
    profile = shop.elbise.profile
    profile.show_on_home = True
    profile.save()

    categories = section("categories")["categories"]

    assert [c["slug"] for c in categories] == [shop.elbise.slug]


def test_a_rich_text_section_shows_its_text_and_is_skipped_when_empty(client, shop):
    save(
        [
            {"id": "a", "type": "rich_text", "settings": {"body": "Merhaba"}},
            {"id": "b", "type": "rich_text", "settings": {"body": "  "}},
        ],
    )

    assert section("a")["body"] == "Merhaba"
    assert ids() == ["a"]


def test_an_unknown_section_type_is_refused():
    with pytest.raises(UnprocessableError):
        homepage.validate([{"id": "x", "type": "carousel3d"}])


def test_unknown_settings_are_refused():
    with pytest.raises(UnprocessableError):
        homepage.validate(
            [{"id": "x", "type": "products", "settings": {"colour": "red"}}],
        )


def test_a_count_outside_the_limits_is_refused():
    for count in (0, 25):
        with pytest.raises(UnprocessableError):
            homepage.validate(
                [{"id": "x", "type": "products", "settings": {"count": count}}],
            )


def test_section_ids_must_be_unique():
    with pytest.raises(UnprocessableError):
        homepage.validate(
            [{"id": "x", "type": "slider"}, {"id": "x", "type": "slider"}],
        )


def test_a_layout_holds_at_most_twenty_sections():
    twenty = [{"id": f"s{n}", "type": "slider"} for n in range(20)]

    assert len(homepage.validate(twenty)) == 20
    with pytest.raises(UnprocessableError):
        homepage.validate([*twenty, {"id": "s20", "type": "slider"}])


def test_the_layout_is_a_single_row():
    homepage.get()

    with pytest.raises(Exception):  # noqa: B017, PT011
        HomepageLayout.objects.create(pk=2, sections=[])
