# ruff: noqa: PLR2004
from __future__ import annotations

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductProfile
from store_backend.services import catalog
from store_backend.services import search

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IŞIK", "isik"),
        ("ışık", "isik"),
        ("İstanbul", "istanbul"),
        ("ISTANBUL", "istanbul"),
        ("Çiçek Şapka Öğün Üzüm", "cicek sapka ogun uzum"),
        ("  Gömlek ", "gomlek"),
    ],
)
def test_fold_treats_dotted_and_dotless_i_and_diacritics_as_plain_letters(
    text,
    expected,
):
    assert search.fold(text) == expected


def make(title, *, brand=None, category=None, colour=None, price="100.00"):
    cat = category or catalog.create_category("Giyim")
    product = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cat,
        shelf_price=price,
        brand=brand,
    )
    catalog.create_variant(
        product,
        colour=colour or catalog.create_colour(f"Renk {title}", "#000000"),
        size="M",
        stock=3,
    )
    return product


def found(q):
    queryset = search.search(ProductProfile.objects.all(), q)
    return {p.product.title for p in queryset.select_related("product")}


@pytest.fixture
def shop():
    brand = catalog.create_brand("Şıklık")
    make("Işıklı Gömlek", brand=brand)
    make("İpek Elbise")
    make("Çiçekli Etek")
    make("Ağaç Desenli Tunik")
    return brand


@pytest.mark.parametrize(
    ("q", "title"),
    [
        ("ışıklı", "Işıklı Gömlek"),
        ("ISIKLI", "Işıklı Gömlek"),
        ("isikli gomlek", "Işıklı Gömlek"),
        ("ipek", "İpek Elbise"),
        ("İPEK", "İpek Elbise"),
        ("cicekli", "Çiçekli Etek"),
        ("ÇİÇEKLİ", "Çiçekli Etek"),
        ("agac", "Ağaç Desenli Tunik"),
        ("ağaç", "Ağaç Desenli Tunik"),
    ],
)
def test_search_finds_products_regardless_of_turkish_letters(shop, q, title):
    assert title in found(q)


def test_search_tolerates_a_typo(shop):
    assert "İpek Elbise" in found("elbse")
    assert "Çiçekli Etek" in found("cicekly")


def test_search_matches_brand_category_and_sku(shop):
    assert "Işıklı Gömlek" in found("siklik")
    assert "İpek Elbise" in found("giyim")
    sku = ProductProfile.objects.get(product__title="İpek Elbise").product.children
    first_sku = sku.first().variant_profile.sku
    assert "İpek Elbise" in found(first_sku)


def test_search_does_not_return_unrelated_products(shop):
    assert found("pantolon") == set()


def test_blank_query_leaves_the_queryset_alone(shop):
    assert len(found("   ")) == 4


def test_renaming_a_product_brand_or_category_updates_the_index():
    brand = catalog.create_brand("Eski")
    cat = catalog.create_category("Ayakkabı")
    product = make("Spor Model", brand=brand, category=cat)

    assert "Spor Model" in found("eski")
    catalog.update_brand(brand, name="Yeni Marka")
    assert "Spor Model" in found("yeni")
    assert "Spor Model" not in found("eski")

    catalog.update_category(cat, name="Terlik")
    assert "Spor Model" in found("terlik")

    catalog.update_product(None, product, title="Kaban")
    assert "Kaban" in found("kaban")
    assert "Spor Model" not in found("spor")
