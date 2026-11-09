# ruff: noqa: PLR2004
from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.partner.models import StockRecord

from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog

pytestmark = pytest.mark.django_db

URL = "/api/store/products"


class Shop:
    def __init__(self):
        self.giyim = catalog.create_category("Giyim")
        self.elbise = catalog.create_category("Elbise", self.giyim)
        self.ayakkabi = catalog.create_category("Ayakkabı")
        self.black = catalog.create_colour("Siyah", "#000000")
        self.red = catalog.create_colour("Kırmızı", "#FF0000")
        self.blue = catalog.create_colour("Mavi", "#0000FF")

    def product(  # noqa: PLR0913
        self,
        title,
        variants,
        *,
        category=None,
        price="100.00",
        old_price=None,
        brand=None,
        status=ProductStatus.PUBLISHED,
        klass="Giyim",
    ):
        product = catalog.create_product(
            title=title,
            product_class=ProductClass.objects.get(name=klass),
            category=category or self.elbise,
            shelf_price=price,
            old_price=old_price,
            brand=brand,
        )
        for colour, size, stock in variants:
            catalog.create_variant(
                product,
                colour=colour,
                size=size,
                stock=stock,
            )
        ProductProfile.objects.filter(product=product).update(status=status)
        return product


@pytest.fixture
def shop():
    return Shop()


def get(client, **params):
    response = client.get(URL, params)
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def titles(data):
    return [item["title"] for item in data["items"]]


def counts(facet, key):
    return {row[key]: row["count"] for row in facet}


def test_list_is_public_and_returns_cards(client, shop):
    product = shop.product(
        "Yazlık Elbise",
        [(shop.black, "M", 3), (shop.red, "S", 2)],
        price="199.90",
        old_price="249.90",
    )
    ProductPhoto.objects.create(
        product=product,
        original="o.jpg",
        width=1200,
        height=900,
        is_primary=True,
        status="ready",
        derivatives=[
            {"width": 800, "name": "b.webp"},
            {"width": 400, "name": "a.webp"},
        ],
    )

    data = get(client)

    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 24
    card = data["items"][0]
    assert card["slug"] == "yazlik-elbise"
    assert card["price"] == "199.90"
    assert card["old_price"] == "249.90"
    assert card["sold_out"] is False
    assert [s["width"] for s in card["image"]["sizes"]] == [400, 800]
    assert card["image"]["alt"] == "Yazlık Elbise"
    assert {c["name"] for c in card["colours"]} == {"Siyah", "Kırmızı"}


def test_draft_and_archived_products_are_hidden(client, shop):
    shop.product("Taslak", [(shop.black, "M", 1)], status=ProductStatus.DRAFT)
    shop.product("Arşiv", [(shop.black, "M", 1)], status=ProductStatus.ARCHIVED)
    shop.product("Yayında", [(shop.black, "M", 1)])

    assert titles(get(client)) == ["Yayında"]


def test_product_page_404s_for_draft_archived_and_unknown(client, shop):
    shop.product("Taslak", [(shop.black, "M", 1)], status=ProductStatus.DRAFT)
    shop.product("Arşiv", [(shop.black, "M", 1)], status=ProductStatus.ARCHIVED)

    for slug in ("taslak", "arsiv", "yok"):
        response = client.get(f"{URL}/{slug}")
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert response.json()["code"] == "not_found"


def test_product_page_has_variants_stock_state_and_breadcrumbs(client, shop):
    shop.product(
        "Elbise",
        [(shop.black, "M", 1), (shop.black, "L", 0), (shop.red, "M", 10)],
    )

    data = client.get(f"{URL}/elbise").json()

    assert [b["slug"] for b in data["breadcrumbs"]] == ["giyim", "elbise"]
    by_key = {(v["colour"]["name"], v["size"]): v for v in data["variants"]}
    assert by_key[("Siyah", "M")]["remaining"] == 1
    assert by_key[("Siyah", "L")]["in_stock"] is False
    assert by_key[("Kırmızı", "M")]["remaining"] is None
    assert data["is_hygiene"] is False


def test_sold_out_products_are_marked_sorted_last_and_hidden_by_the_stock_toggle(
    client,
    shop,
):
    shop.product("Biten", [(shop.black, "M", 0)])
    shop.product("Var", [(shop.black, "M", 5)])

    default = get(client)
    assert titles(default) == ["Var"]

    everything = get(client, in_stock=False)
    assert titles(everything) == ["Var", "Biten"]
    assert everything["items"][1]["sold_out"] is True


def test_reserved_units_do_not_count_as_available(client, shop):
    product = shop.product("Rezerve", [(shop.black, "M", 2)])
    StockRecord.objects.filter(product__parent=product).update(num_allocated=2)

    assert titles(get(client)) == []
    assert get(client, in_stock=False)["items"][0]["sold_out"] is True


def test_category_page_includes_child_categories(client, shop):
    shop.product("Elbise A", [(shop.black, "M", 1)])
    shop.product("Bot", [(shop.black, "M", 1)], category=shop.ayakkabi)

    assert titles(get(client, category="giyim")) == ["Elbise A"]
    assert titles(get(client, category="elbise")) == ["Elbise A"]
    assert titles(get(client, category="ayakkabi")) == ["Bot"]
    assert client.get(URL, {"category": "yok"}).status_code == HTTPStatus.NOT_FOUND


def test_colour_and_size_filters_need_one_variant_to_satisfy_both(client, shop):
    # Black exists in M and red exists in L, but nothing is black AND L.
    shop.product("Karışık", [(shop.black, "M", 2), (shop.red, "L", 2)])
    shop.product("Siyah L", [(shop.black, "L", 2)])

    both = get(client, colour=shop.black.pk, size="L")
    assert titles(both) == ["Siyah L"]
    assert sorted(titles(get(client, colour=shop.black.pk))) == ["Karışık", "Siyah L"]
    assert sorted(titles(get(client, size="L"))) == ["Karışık", "Siyah L"]


def test_colour_filter_ignores_variants_that_are_out_of_stock(client, shop):
    shop.product("Biten siyah", [(shop.black, "M", 0), (shop.red, "M", 3)])

    assert titles(get(client, colour=shop.black.pk)) == []
    assert titles(get(client, colour=shop.black.pk, in_stock=False)) == ["Biten siyah"]


def test_brand_and_price_range_filters(client, shop):
    nike = catalog.create_brand("Nike")
    shop.product("Ucuz", [(shop.black, "M", 1)], price="50.00", brand=nike)
    shop.product("Pahalı", [(shop.black, "M", 1)], price="500.00")

    assert titles(get(client, brand=nike.pk)) == ["Ucuz"]
    assert titles(get(client, price_min="100")) == ["Pahalı"]
    assert titles(get(client, price_max="100")) == ["Ucuz"]
    assert sorted(titles(get(client, price_min="50", price_max="500"))) == [
        "Pahalı",
        "Ucuz",
    ]


def test_sorting(client, shop):
    shop.product("A", [(shop.black, "M", 1)], price="300.00")
    shop.product("B", [(shop.black, "M", 1)], price="100.00")
    shop.product("C", [(shop.black, "M", 1)], price="200.00")

    assert titles(get(client)) == ["C", "B", "A"]
    assert titles(get(client, sort="price_asc")) == ["B", "C", "A"]
    assert titles(get(client, sort="price_desc")) == ["A", "C", "B"]


def test_variant_price_override_drives_price_and_hides_old_price(client, shop):
    product = shop.product(
        "Farklı",
        [(shop.black, "M", 1), (shop.red, "M", 1)],
        price="200.00",
        old_price="250.00",
    )
    variant = product.children.filter(variant_profile__colour=shop.red).get()
    catalog.update_variant(None, variant, price_override="150.00")

    card = get(client)["items"][0]
    assert card["price"] == "150.00"
    assert card["old_price"] is None
    assert titles(get(client, price_max="160")) == ["Farklı"]


def test_search_with_turkish_letters_and_typos(client, shop):
    shop.product("Işıklı Gömlek", [(shop.black, "M", 1)], category=shop.ayakkabi)
    shop.product("İpek Elbise", [(shop.black, "M", 1)])

    assert titles(get(client, q="isikli")) == ["Işıklı Gömlek"]
    assert titles(get(client, q="IŞIKLI GÖMLEK")) == ["Işıklı Gömlek"]
    assert titles(get(client, q="ipek")) == ["İpek Elbise"]
    assert titles(get(client, q="elbse")) == ["İpek Elbise"]
    assert titles(get(client, q="pantolon")) == []


def test_search_orders_by_relevance_and_combines_with_filters(client, shop):
    shop.product("Mavi Etek", [(shop.black, "M", 1)])
    shop.product("Mavi Elbise Mavi", [(shop.blue, "S", 1)])

    data = get(client, q="mavi elbise", sort="relevance")
    assert titles(data)[0] == "Mavi Elbise Mavi"
    assert titles(get(client, q="mavi", size="M")) == ["Mavi Etek"]


def test_facet_counts_follow_the_other_filters(client, shop):
    nike = catalog.create_brand("Nike")
    adidas = catalog.create_brand("Adidas")
    shop.product("P1", [(shop.black, "M", 1), (shop.red, "L", 1)], brand=nike)
    shop.product("P2", [(shop.black, "M", 1), (shop.black, "L", 1)], brand=adidas)
    shop.product("P3", [(shop.red, "S", 1)], brand=nike, price="400.00")

    unfiltered = get(client)["facets"]
    assert counts(unfiltered["colours"], "name") == {"Siyah": 2, "Kırmızı": 2}
    assert counts(unfiltered["sizes"], "size") == {"S": 1, "M": 2, "L": 2}
    assert counts(unfiltered["brands"], "name") == {"Nike": 2, "Adidas": 1}
    assert unfiltered["price"] == {"min": "100.00", "max": "400.00"}

    # Colour red: other dimensions shrink; the colour facet keeps its own alternatives.
    red = get(client, colour=shop.red.pk)["facets"]
    assert counts(red["colours"], "name") == {"Siyah": 2, "Kırmızı": 2}
    assert counts(red["sizes"], "size") == {"L": 1, "S": 1}
    assert counts(red["brands"], "name") == {"Nike": 2}

    # Size L narrows colours (red L is P1 only), and the size facet stays whole.
    large = get(client, size="L")["facets"]
    assert counts(large["colours"], "name") == {"Siyah": 1, "Kırmızı": 1}
    assert counts(large["sizes"], "size") == {"S": 1, "M": 2, "L": 2}
    assert counts(large["brands"], "name") == {"Adidas": 1, "Nike": 1}

    # Brand facet ignores the brand filter itself but respects colour.
    nike_black = get(client, brand=nike.pk, colour=shop.black.pk)["facets"]
    assert counts(nike_black["brands"], "name") == {"Nike": 1, "Adidas": 1}
    assert counts(nike_black["colours"], "name") == {"Siyah": 1, "Kırmızı": 2}


def test_facets_respect_search_and_category(client, shop):
    shop.product("Mavi Elbise", [(shop.blue, "M", 1)])
    shop.product("Bot", [(shop.black, "M", 1)], category=shop.ayakkabi)

    facets = get(client, category="giyim")["facets"]
    assert counts(facets["colours"], "name") == {"Mavi": 1}
    facets = get(client, q="bot")["facets"]
    assert counts(facets["colours"], "name") == {"Siyah": 1}


def test_paging_is_24_per_page(client, shop):
    for i in range(30):
        shop.product(f"Ürün {i:02d}", [(shop.black, "M", 1)])

    first = get(client)
    second = get(client, page=2)

    assert first["total"] == 30
    assert len(first["items"]) == 24
    assert len(second["items"]) == 6
    assert not set(titles(first)) & set(titles(second))


def test_invalid_sort_is_rejected(client, shop):
    assert (
        client.get(URL, {"sort": "zzz"}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_list_query_count_does_not_grow_with_the_page(
    client,
    shop,
    django_assert_max_num_queries,
):
    for i in range(12):
        shop.product(f"Ürün {i}", [(shop.black, "M", 1), (shop.red, "L", 1)])

    with django_assert_max_num_queries(20):
        get(client)
