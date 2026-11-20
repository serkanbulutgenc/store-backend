"""Recently viewed (GLOSSARY: Recently viewed): the batch endpoint that turns the
slugs a browser kept into fresh cards. The list lives in the browser; the server
stores nothing."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.test_campaign_storefront import campaign
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import browse

pytestmark = pytest.mark.django_db

RECENT = "/api/store/recently-viewed"


@pytest.fixture
def shop():
    return Shop()


def viewed(client, *slugs: str):
    return client.get(RECENT, {"slugs": ",".join(slugs)})


def titles(response) -> list[str]:
    assert response.status_code == HTTPStatus.OK, response.content
    return [card["title"] for card in response.json()]


def test_cards_come_back_in_the_order_the_browser_sent_the_slugs(client, shop):
    a = shop.product("A", [(shop.black, "M", 3)])
    b = shop.product("B", [(shop.black, "M", 3)])
    c = shop.product("C", [(shop.black, "M", 3)])

    response = viewed(client, c.profile.slug, a.profile.slug, b.profile.slug)

    assert titles(response) == ["C", "A", "B"]


def test_unknown_unpublished_and_sold_out_products_drop_out(client, shop):
    kept = shop.product("Stokta", [(shop.black, "M", 3)])
    draft = shop.product("Taslak", [(shop.black, "M", 3)], status=ProductStatus.DRAFT)
    sold_out = shop.product("Tükenmiş", [(shop.black, "M", 0)])

    response = viewed(
        client,
        draft.profile.slug,
        "yok-boyle-bir-urun",
        sold_out.profile.slug,
        kept.profile.slug,
    )

    assert titles(response) == ["Stokta"]


def test_more_than_twelve_slugs_is_a_validation_error(client, shop):
    twelve = [f"urun-{n}" for n in range(12)]

    assert viewed(client, *twelve).status_code == HTTPStatus.OK
    response = viewed(client, *twelve, "urun-12")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "slugs" in response.json()["fields"]


def test_a_card_shows_the_campaign_price_that_applies_now(client, shop):
    product = shop.product("İndirimli", [(shop.black, "M", 3)], price="100.00")
    campaign(20, product)

    (card,) = viewed(client, product.profile.slug).json()

    assert card["price"] == "80.00"
    assert card["shelf_price"] == "100.00"
    assert card["campaign_percentage"] == "20.00"


def test_the_service_refuses_more_than_twelve_slugs_and_keeps_duplicates_once(shop):
    product = shop.product("Tek", [(shop.black, "M", 3)])
    slug = product.profile.slug

    assert [c["title"] for c in browse.recently_viewed([slug, slug])] == ["Tek"]
    with pytest.raises(UnprocessableError):
        browse.recently_viewed([f"urun-{n}" for n in range(13)])
