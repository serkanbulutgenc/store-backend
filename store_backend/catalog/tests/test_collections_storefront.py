"""The public Collection page and the Collection as a Menu target (GLOSSARY: Collection,
docs/adr/0007)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.shop.models import Menu
from store_backend.shop.models import MenuItem

pytestmark = pytest.mark.django_db

MENUS = "/api/store/menus"


def page(slug):
    return Client().get(f"/api/store/collections/{slug}")


def make_product(title="Elbise", *, status=ProductStatus.PUBLISHED, stock=5):
    made = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
    )
    catalog.create_variant(
        made,
        colour=catalog.create_colour(f"Renk {title}", "#112233"),
        size="M",
        stock=stock,
    )
    ProductProfile.objects.filter(product=made).update(status=status)
    return made


def make_collection(
    *products,
    name="Editörün seçimi",
    status=CollectionStatus.PUBLISHED,
    **fields,
):
    collection = Collection.objects.create(
        name=name,
        slug=fields.pop("slug", "editorun-secimi"),
        status=status,
        **fields,
    )
    for position, product in enumerate(products):
        CollectionProduct.objects.create(
            collection=collection,
            product=product,
            position=position,
        )
    return collection


def titles(response):
    return [i["title"] for i in response.json()["items"]]


def header_menu():
    return Client().get(MENUS).json()["header"]


def in_header(collection, label=""):
    MenuItem.objects.filter(menu__key="header").delete()
    return MenuItem.objects.create(
        menu=Menu.objects.get(key="header"),
        target_type="collection",
        collection=collection,
        label=label,
    )


# --- the page ------------------------------------------------------------------------


def test_a_published_collection_shows_its_copy_and_products_in_the_owners_order():
    first, second, third = (make_product(t) for t in ("Bir", "Iki", "Uc"))
    make_collection(third, first, second, description="Yeni sezon")

    response = page("editorun-secimi")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["name"] == "Editörün seçimi"
    assert body["slug"] == "editorun-secimi"
    assert body["description"] == "Yeni sezon"
    assert titles(response) == ["Uc", "Bir", "Iki"]
    assert body["items"][0]["slug"]
    assert body["items"][0]["sold_out"] is False


@pytest.mark.parametrize(
    "status",
    [CollectionStatus.DRAFT, CollectionStatus.UNPUBLISHED],
)
def test_a_collection_that_is_not_published_is_not_found(status):
    make_collection(make_product(), status=status)

    assert page("editorun-secimi").status_code == HTTPStatus.NOT_FOUND


def test_an_unknown_slug_is_not_found():
    assert page("yok").status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.ARCHIVED])
def test_draft_and_archived_products_are_hidden(status):
    shown = make_product("Gorunen")
    hidden = make_product("Gizli", status=status)
    make_collection(hidden, shown)

    assert titles(page("editorun-secimi")) == ["Gorunen"]


def test_a_sold_out_product_stays_and_is_flagged():
    available = make_product("Var")
    gone = make_product("Yok", stock=0)
    make_collection(gone, available)

    items = page("editorun-secimi").json()["items"]

    assert [(i["title"], i["sold_out"]) for i in items] == [
        ("Yok", True),
        ("Var", False),
    ]


def test_a_collection_with_no_visible_products_is_not_found():
    make_collection(make_product(status=ProductStatus.DRAFT))
    make_collection(name="Bos", slug="bos")

    assert page("editorun-secimi").status_code == HTTPStatus.NOT_FOUND
    assert page("bos").status_code == HTTPStatus.NOT_FOUND


def test_a_product_returning_to_published_brings_the_collection_back():
    product = make_product(status=ProductStatus.ARCHIVED)
    make_collection(product)
    assert page("editorun-secimi").status_code == HTTPStatus.NOT_FOUND

    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )

    assert page("editorun-secimi").status_code == HTTPStatus.OK


# --- the Menu target -----------------------------------------------------------------


def test_a_collection_item_links_to_the_page_and_follows_the_name():
    collection = make_collection(make_product())
    in_header(collection)

    assert [(n["label"], n["url"]) for n in header_menu()] == [
        ("Editörün seçimi", "/collections/editorun-secimi"),
    ]

    collection.name = "Sonbahar"
    collection.slug = "sonbahar"
    collection.save()

    assert [(n["label"], n["url"]) for n in header_menu()] == [
        ("Sonbahar", "/collections/sonbahar"),
    ]


def test_the_owners_label_overrides_the_collections_name():
    in_header(make_collection(make_product()), label="Seçtiklerimiz")

    assert [n["label"] for n in header_menu()] == ["Seçtiklerimiz"]


@pytest.mark.parametrize(
    "status",
    [CollectionStatus.DRAFT, CollectionStatus.UNPUBLISHED],
)
def test_a_collection_that_is_not_published_is_left_out_of_the_menu(status):
    in_header(make_collection(make_product(), status=status))

    assert header_menu() == []


def test_a_collection_with_no_visible_products_is_left_out_of_the_menu():
    in_header(make_collection(make_product(status=ProductStatus.DRAFT)))
    assert header_menu() == []

    empty = make_collection(name="Bos", slug="bos")
    in_header(empty)
    assert header_menu() == []


def test_a_collection_shown_in_the_menu_opens():
    collection = make_collection(make_product())
    in_header(collection)

    (node,) = header_menu()

    assert page(node["url"].removeprefix("/collections/")).status_code == (
        HTTPStatus.OK
    )
