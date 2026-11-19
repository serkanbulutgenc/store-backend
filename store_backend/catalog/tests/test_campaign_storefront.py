"""A running Campaign changes what the Storefront shows (GLOSSARY: Campaign): the lower
price, the Shelf price to strike through and the percentage; never the Old price."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone

from store_backend.catalog.models import Campaign
from store_backend.catalog.tests.test_storefront_api import URL
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.catalog.tests.test_storefront_api import get
from store_backend.catalog.tests.test_storefront_api import titles
from store_backend.services import catalog

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop():
    return Shop()


def campaign(percentage, *products, **fields):
    made = Campaign.objects.create(
        name=fields.pop("name", f"-{percentage}"),
        percentage=percentage,
        **fields,
    )
    made.products.set(products)
    return made


def test_a_listing_card_shows_the_campaign_price_and_hides_the_old_price(client, shop):
    product = shop.product(
        "Yazlık Elbise",
        [(shop.black, "M", 3)],
        price="199.90",
        old_price="249.90",
    )
    campaign(20, product)

    card = get(client)["items"][0]

    assert card["price"] == "159.92"
    assert card["shelf_price"] == "199.90"
    assert card["campaign_percentage"] == "20.00"
    assert card["old_price"] is None


def test_a_card_without_a_campaign_is_unchanged(client, shop):
    shop.product("Düz", [(shop.black, "M", 1)], price="100.00", old_price="120.00")

    card = get(client)["items"][0]

    assert card["price"] == "100.00"
    assert card["old_price"] == "120.00"
    assert card["shelf_price"] is None
    assert card["campaign_percentage"] is None


def test_the_product_page_prices_each_variant_and_its_override(client, shop):
    product = shop.product(
        "Farklı",
        [(shop.black, "M", 1), (shop.red, "M", 1)],
        price="200.00",
        old_price="250.00",
    )
    red = product.children.filter(variant_profile__colour=shop.red).get()
    catalog.update_variant(None, red, price_override="150.00")
    campaign(10, product)

    page = client.get(f"{URL}/{product.profile.slug}").json()

    by_colour = {v["colour"]["name"]: v for v in page["variants"]}
    assert by_colour["Siyah"]["price"] == "180.00"
    assert by_colour["Siyah"]["shelf_price"] == "200.00"
    assert by_colour["Kırmızı"]["price"] == "135.00"
    assert by_colour["Kırmızı"]["shelf_price"] == "150.00"
    assert page["price"] == "135.00"
    assert page["shelf_price"] == "150.00"
    assert page["campaign_percentage"] == "10.00"
    assert page["old_price"] is None


def test_price_filter_sort_and_range_go_by_the_campaign_price(client, shop):
    cheap = shop.product("Ucuz", [(shop.black, "M", 1)], price="150.00")
    dear = shop.product("Pahalı", [(shop.black, "M", 1)], price="200.00")
    campaign(50, dear)  # 100.00 after the Campaign, under Ucuz

    assert titles(get(client, sort="price_asc")) == ["Pahalı", "Ucuz"]
    assert titles(get(client, sort="price_desc")) == ["Ucuz", "Pahalı"]
    assert titles(get(client, price_max="120")) == ["Pahalı"]
    assert titles(get(client, price_min="120")) == ["Ucuz"]
    assert get(client)["facets"]["price"] == {"min": "100.00", "max": "150.00"}
    assert cheap.pk  # untouched Product keeps its Shelf price
    assert get(client, price_min="120")["items"][0]["price"] == "150.00"


def test_search_results_carry_the_campaign_price(client, shop):
    product = shop.product("Işıklı Gömlek", [(shop.black, "M", 1)], price="100.00")
    campaign(25, product)

    card = get(client, q="gomlek")["items"][0]

    assert card["price"] == "75.00"
    assert card["shelf_price"] == "100.00"


def test_only_a_campaign_in_force_prices_the_listing(client, shop):
    product = shop.product("Elbise", [(shop.black, "M", 1)], price="100.00")
    now = timezone.now()
    campaign(20, product, starts_at=now + timedelta(hours=1))
    campaign(30, product, ends_at=now - timedelta(hours=1))
    campaign(40, product, is_active=False)

    assert get(client)["items"][0]["price"] == "100.00"


def test_a_campaign_leaves_draft_and_archived_rules_alone(client, shop):
    draft = shop.product("Taslak", [(shop.black, "M", 1)], status="draft")
    archived = shop.product("Arşiv", [(shop.black, "M", 1)], status="archived")
    live = shop.product("Yayında", [(shop.black, "M", 1)])
    campaign(20, draft, archived, live)

    assert titles(get(client)) == ["Yayında"]
    for hidden in (draft, archived):
        response = client.get(f"{URL}/{hidden.profile.slug}")
        assert response.status_code == HTTPStatus.NOT_FOUND


def test_campaigns_do_not_add_queries_per_product(
    client,
    shop,
    django_assert_max_num_queries,
):
    products = [
        shop.product(f"Ürün {i}", [(shop.black, "M", 1), (shop.red, "L", 1)])
        for i in range(12)
    ]
    campaign(20, *products[:6])
    campaign(30, *products[3:])

    with django_assert_max_num_queries(35):
        get(client)
