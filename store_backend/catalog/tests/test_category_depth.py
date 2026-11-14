# ruff: noqa: PLR2004
"""Storefront browse, menu, search and sitemap at every tree depth (ticket 70)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import categories

pytestmark = pytest.mark.django_db

STORE = "/api/store"


class Tree:
    """Kadın > Giyim > Elbise > Midi > Yazlık, plus a second root."""

    def __init__(self):
        self.root = catalog.create_category("Kadın")
        self.level2 = catalog.create_category("Giyim", self.root)
        self.level3 = catalog.create_category("Elbise", self.level2)
        self.level4 = catalog.create_category("Midi", self.level3)
        self.level5 = catalog.create_category("Yazlık", self.level4)
        self.other = catalog.create_category("Ayakkabı")
        self.black = catalog.create_colour("Siyah", "#000000")

    def product(self, title, category):
        product = catalog.create_product(
            title=title,
            product_class=ProductClass.objects.get(name="Giyim"),
            category=category,
            shelf_price="100.00",
        )
        catalog.create_variant(product, colour=self.black, size="M", stock=3)
        ProductProfile.objects.filter(product=product).update(
            status=ProductStatus.PUBLISHED,
        )
        return product


@pytest.fixture
def tree():
    return Tree()


def names(nodes):
    return [n["name"] for n in nodes]


# --- menu ------------------------------------------------------------------------


def test_the_menu_shows_two_levels_by_default_with_child_counts(client, tree):
    nodes = client.get(f"{STORE}/categories").json()

    assert names(nodes) == ["Kadın", "Ayakkabı"]
    kadin = nodes[0]
    assert kadin["child_count"] == 1
    assert names(kadin["children"]) == ["Giyim"]
    assert kadin["children"][0]["child_count"] == 1
    assert kadin["children"][0]["children"] == []
    assert nodes[1]["child_count"] == 0


def test_the_menu_depth_parameter_returns_deeper_levels(client, tree):
    nodes = client.get(f"{STORE}/categories", {"depth": 5}).json()

    node = nodes[0]
    for expected in ("Kadın", "Giyim", "Elbise", "Midi", "Yazlık"):
        assert node["name"] == expected
        children = node["children"]
        node = children[0] if children else None
    assert node is None


@pytest.mark.parametrize("depth", [0, 6])
def test_the_menu_depth_must_be_between_one_and_five(client, tree, depth):
    response = client.get(f"{STORE}/categories", {"depth": depth})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_hidden_category_takes_its_subtree_off_the_menu(client, tree):
    categories.update(tree.level2, {"is_hidden": True})

    nodes = client.get(f"{STORE}/categories", {"depth": 5}).json()

    kadin = nodes[0]
    assert kadin["children"] == []
    assert kadin["child_count"] == 0


# --- category page ---------------------------------------------------------------


def test_a_depth_four_category_returns_children_and_the_full_breadcrumb(client, tree):
    page = client.get(f"{STORE}/categories/{tree.level4.slug}").json()

    assert names(page["breadcrumbs"]) == ["Kadın", "Giyim", "Elbise"]
    assert names(page["children"]) == ["Yazlık"]


def test_hiding_a_depth_two_category_hides_depths_three_to_five(client, tree):
    categories.update(tree.level2, {"is_hidden": True})

    for hidden in (tree.level2, tree.level3, tree.level4, tree.level5):
        response = client.get(f"{STORE}/categories/{hidden.slug}")
        assert response.status_code == HTTPStatus.NOT_FOUND, hidden.name
    assert client.get(f"{STORE}/categories/{tree.root.slug}").status_code == 200


# --- listing ---------------------------------------------------------------------


def listed(client, **params):
    response = client.get(f"{STORE}/products", params)
    assert response.status_code == HTTPStatus.OK, response.content
    return sorted(item["title"] for item in response.json()["items"])


def test_a_mid_level_category_lists_the_products_of_all_its_descendants(client, tree):
    tree.product("Üst", tree.root)
    tree.product("Orta", tree.level3)
    tree.product("Derin", tree.level5)
    tree.product("Başka", tree.other)

    assert listed(client, category=tree.level3.slug) == ["Derin", "Orta"]
    assert listed(client, category=tree.level2.slug) == ["Derin", "Orta"]
    assert listed(client, category=tree.root.slug) == ["Derin", "Orta", "Üst"]
    assert listed(client, category=tree.level5.slug) == ["Derin"]


def test_a_hidden_descendant_is_left_out_of_its_ancestors_listing(client, tree):
    tree.product("Orta", tree.level3)
    tree.product("Derin", tree.level5)
    categories.update(tree.level4, {"is_hidden": True})

    assert listed(client, category=tree.level3.slug) == ["Orta"]
    response = client.get(f"{STORE}/products", {"category": tree.level5.slug})
    assert response.status_code == HTTPStatus.NOT_FOUND


# --- search ----------------------------------------------------------------------


def test_a_product_is_found_by_the_name_of_any_ancestor_category(client, tree):
    tree.product("Çiçekli", tree.level5)
    tree.product("Başka", tree.other)

    for ancestor in ("Kadın", "Giyim", "Elbise", "Midi", "Yazlık"):
        assert listed(client, q=ancestor) == ["Çiçekli"], ancestor


def test_renaming_an_ancestor_reindexes_the_products_below_it(client, tree):
    tree.product("Çiçekli", tree.level5)

    catalog.update_category(tree.level2, name="Tekstil")

    assert listed(client, q="Tekstil") == ["Çiçekli"]
    assert listed(client, q="Giyim") == []


# --- sitemap ---------------------------------------------------------------------


def sitemap(client) -> str:
    response = client.get("/sitemap.xml", headers={"x-forwarded-proto": "https"})
    assert response.status_code == HTTPStatus.OK
    return response.content.decode()


def test_the_sitemap_lists_deep_categories_and_drops_hidden_and_deleted_ones(
    client,
    tree,
):
    body = sitemap(client)
    for node in (tree.root, tree.level3, tree.level5):
        assert f"/kategori/{node.slug}</loc>" in body

    categories.update(tree.level4, {"is_hidden": True})
    body = sitemap(client)
    assert f"/kategori/{tree.level3.slug}</loc>" in body
    assert f"/kategori/{tree.level4.slug}</loc>" not in body
    assert f"/kategori/{tree.level5.slug}</loc>" not in body

    categories.delete(owner_of(), tree.other)
    assert f"/kategori/{tree.other.slug}</loc>" not in sitemap(client)


def owner_of():
    from store_backend.staff.tests.conftest import staff_member  # noqa: PLC0415

    return staff_member("Owner")
