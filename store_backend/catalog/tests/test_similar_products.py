"""Similar products (GLOSSARY: Similar products): the ranked "Benzer ürünler" list of a
Product page, read through the store API."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.test_campaign_storefront import campaign
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.catalog.tests.test_storefront_api import _sell as sell
from store_backend.conftest import make_verified_user
from store_backend.services import attributes
from store_backend.services import catalog

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/store/products"


@pytest.fixture
def shop():
    return Shop()


def similar(client, product) -> list[str]:
    """The titles of the Products shown as similar to `product`, in order."""
    response = client.get(f"{PRODUCTS}/{product.profile.slug}/similar")
    assert response.status_code == HTTPStatus.OK, response.content
    return [card["title"] for card in response.json()]


def test_products_of_the_same_category_are_similar_and_the_product_itself_is_not(
    client,
    shop,
):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    shop.product("Komşu elbise", [(shop.black, "M", 3)])

    assert similar(client, current) == ["Komşu elbise"]


def test_only_published_products_with_stock_are_similar(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    shop.product("Stokta", [(shop.black, "M", 1)])
    shop.product("Tükenmiş", [(shop.black, "M", 0)])
    shop.product("Taslak", [(shop.black, "M", 3)], status=ProductStatus.DRAFT)

    assert similar(client, current) == ["Stokta"]


def test_a_product_of_the_same_brand_ranks_first(client, shop):
    ours, theirs = catalog.create_brand("Aura"), catalog.create_brand("Başka")
    current = shop.product("Bu elbise", [(shop.black, "M", 3)], brand=ours)
    # Created in the middle, so neither oldest-first nor newest-first puts it on top.
    shop.product("Başka marka", [(shop.black, "M", 3)], brand=theirs)
    shop.product("Aynı marka", [(shop.black, "M", 3)], brand=ours)
    shop.product("Markasız", [(shop.black, "M", 3)])

    assert similar(client, current)[0] == "Aynı marka"


@pytest.fixture
def fabric(shop):
    """A multi-choice "Kumaş" attribute of Giyim; `give` sets a Product's options."""
    actor = make_verified_user(is_staff=True)
    giyim = ProductClass.objects.get(name="Giyim")
    attribute = attributes.create(
        actor,
        giyim.pk,
        name="Kumaş",
        kind="option",
        is_multiple=True,
        options=["Pamuk", "Keten", "Viskon"],
    )
    ids = {o["label"]: o["id"] for o in attribute["options"]}

    def give(product, *labels):
        attributes.set_values(
            product,
            [
                attributes.ValueInput(
                    attribute_id=attribute["id"],
                    option_ids=tuple(ids[label] for label in labels),
                ),
            ],
        )

    return give


def test_products_sharing_attribute_values_rank_above_those_that_do_not(
    client,
    shop,
    fabric,
):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    fabric(current, "Pamuk")
    # The match is created in the middle, as in the Brand test.
    fabric(shop.product("Keten", [(shop.black, "M", 3)]), "Keten")
    fabric(shop.product("Pamuk", [(shop.black, "M", 3)]), "Pamuk")
    shop.product("Boş", [(shop.black, "M", 3)])

    assert similar(client, current)[0] == "Pamuk"


def test_more_shared_attribute_values_rank_higher(client, shop, fabric):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    fabric(current, "Pamuk", "Keten")
    fabric(shop.product("Bir ortak", [(shop.black, "M", 3)]), "Pamuk")
    fabric(shop.product("İki ortak", [(shop.black, "M", 3)]), "Pamuk", "Keten")
    fabric(shop.product("Ortak yok", [(shop.black, "M", 3)]), "Viskon")

    assert similar(client, current) == ["İki ortak", "Bir ortak", "Ortak yok"]


def test_a_price_within_thirty_percent_ranks_higher(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)], price="100.00")
    shop.product("Pahalı", [(shop.black, "M", 3)], price="131.00")
    shop.product("Yakın", [(shop.black, "M", 3)], price="130.00")
    shop.product("Ucuz", [(shop.black, "M", 3)], price="69.00")

    assert similar(client, current)[0] == "Yakın"


def test_the_price_bonus_is_small_next_to_a_shared_brand(client, shop):
    ours = catalog.create_brand("Aura")
    current = shop.product(
        "Bu elbise",
        [(shop.black, "M", 3)],
        price="100.00",
        brand=ours,
    )
    shop.product("Yakın fiyat", [(shop.black, "M", 3)], price="100.00")
    shop.product(
        "Aynı marka, uzak fiyat",
        [(shop.black, "M", 3)],
        price="500.00",
        brand=ours,
    )

    assert similar(client, current)[0] == "Aynı marka, uzak fiyat"


def test_popularity_breaks_ties_and_ignores_cancelled_orders(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    few = shop.product("Az", [(shop.black, "M", 9)])
    many = shop.product("Çok", [(shop.black, "M", 9)])
    cancelled = shop.product("İptal", [(shop.black, "M", 9)])
    shop.product("Hiç", [(shop.black, "M", 9)])
    sell(few, 1, "710001")
    sell(many, 5, "710002")
    sell(cancelled, 50, "710003", status="cancelled")

    assert similar(client, current) == ["Çok", "Az", "Hiç", "İptal"]


def test_fewer_than_four_in_the_category_widens_to_the_parent_with_the_category_first(
    client,
    shop,
):
    etek = catalog.create_category("Etek", shop.giyim)
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    # The same-Category Product is the oldest: only the Category tier puts it first.
    shop.product("Elbise komşu", [(shop.black, "M", 3)])
    shop.product("Etek bir", [(shop.black, "M", 3)], category=etek)
    shop.product("Etek iki", [(shop.black, "M", 3)], category=etek)
    shop.product("Ayakkabı", [(shop.black, "M", 3)], category=shop.ayakkabi)

    assert similar(client, current) == ["Elbise komşu", "Etek iki", "Etek bir"]


def test_four_in_the_category_do_not_widen(client, shop):
    etek = catalog.create_category("Etek", shop.giyim)
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    for n in range(4):
        shop.product(f"Elbise {n}", [(shop.black, "M", 3)])
    shop.product("Etek", [(shop.black, "M", 3)], category=etek)

    assert "Etek" not in similar(client, current)


def test_at_most_eight_are_shown_newest_first_when_nothing_else_separates_them(
    client,
    shop,
):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    for n in range(10):
        shop.product(f"Komşu {n}", [(shop.black, "M", 3)])

    assert similar(client, current) == [f"Komşu {n}" for n in range(9, 1, -1)]


def test_a_product_with_no_similar_products_gets_an_empty_list(client, shop):
    alone = shop.product("Yalnız", [(shop.black, "M", 3)], category=shop.ayakkabi)
    shop.product("Başka kategori", [(shop.black, "M", 3)])

    assert similar(client, alone) == []


def test_widening_goes_one_level_up_and_no_further(client, shop):
    kapsam = catalog.create_category("Kapşonlu", shop.elbise)  # a child of Elbise
    current = shop.product("Bu kapşonlu", [(shop.black, "M", 3)], category=kapsam)
    shop.product("Elbise", [(shop.black, "M", 3)])  # in Elbise, the parent
    shop.product(
        "Etek",
        [(shop.black, "M", 3)],
        category=catalog.create_category("Etek", shop.giyim),
    )

    assert similar(client, current) == ["Elbise"]


def test_products_of_a_hidden_category_are_not_shown(client, shop):
    gizli = catalog.create_category("Gizli", shop.giyim)
    CategoryProfile.objects.update_or_create(
        category=gizli,
        defaults={"is_hidden": True},
    )
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    shop.product("Görünür", [(shop.black, "M", 3)])
    shop.product("Gizli ürün", [(shop.black, "M", 3)], category=gizli)

    assert similar(client, current) == ["Görünür"]


def test_a_product_only_in_a_hidden_category_has_no_similar_products(client, shop):
    gizli = catalog.create_category("Gizli", shop.giyim)
    CategoryProfile.objects.update_or_create(
        category=gizli,
        defaults={"is_hidden": True},
    )
    current = shop.product("Gizli ürün", [(shop.black, "M", 3)], category=gizli)
    shop.product("Elbise", [(shop.black, "M", 3)])

    assert similar(client, current) == []


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.ARCHIVED])
def test_an_unpublished_or_unknown_product_is_not_found(client, shop, status):
    draft = shop.product("Taslak", [(shop.black, "M", 3)], status=status)

    assert (
        client.get(f"{PRODUCTS}/{draft.profile.slug}/similar").status_code
        == HTTPStatus.NOT_FOUND
    )
    assert client.get(f"{PRODUCTS}/yok/similar").status_code == HTTPStatus.NOT_FOUND


def test_cards_carry_the_campaign_price_and_the_shelf_price(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    discounted = shop.product("İndirimli", [(shop.black, "M", 3)], price="200.00")
    campaign(20, discounted)

    response = client.get(f"{PRODUCTS}/{current.profile.slug}/similar")

    (card,) = response.json()
    assert card["title"] == "İndirimli"
    assert card["price"] == "160.00"
    assert card["shelf_price"] == "200.00"
    assert card["campaign_percentage"] == "20.00"
    assert {"slug", "image", "colours", "sold_out"} <= card.keys()


def test_the_number_of_queries_does_not_grow_with_the_number_of_products(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    url = f"{PRODUCTS}/{current.profile.slug}/similar"

    def count() -> int:
        cache.clear()  # a cached answer would make no queries at all
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url).status_code == HTTPStatus.OK
        return len(queries)

    # Four are enough not to widen, so both counts take the same path; the first request
    # warms what Django caches (content types).
    for n in range(4):
        shop.product(f"Az {n}", [(shop.black, "M", 3), (shop.red, "S", 3)])
    count()
    few = count()
    for n in range(6):
        shop.product(f"Çok {n}", [(shop.black, "M", 3), (shop.red, "S", 3)])

    assert count() == few


def test_the_list_is_cached_for_a_short_time(client, shop):
    current = shop.product("Bu elbise", [(shop.black, "M", 3)])
    shop.product("İlk", [(shop.black, "M", 3)])
    assert similar(client, current) == ["İlk"]

    shop.product("Sonradan", [(shop.black, "M", 3)])

    assert similar(client, current) == ["İlk"]  # still the cached list
    cache.clear()  # standing in for the cache expiring
    assert similar(client, current) == ["Sonradan", "İlk"]
