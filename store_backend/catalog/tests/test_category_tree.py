# ruff: noqa: PLR2004
"""The category tree: up to five levels, Owner-only moves and deletes (ticket 69)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import PendingFileRemoval
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin"


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


def product_in(title, category):
    return catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=category,
        shelf_price="100.00",
    )


def add(api, name, parent=None):
    return api.post(
        f"{ADMIN}/categories",
        {"name": name, "parent_id": parent["id"] if parent else None},
    )


def test_the_tree_may_be_five_levels_deep_and_no_deeper(owner_api):
    node = None
    for level in range(1, 6):
        response = add(owner_api, f"Seviye {level}", node)
        assert response.status_code == HTTPStatus.CREATED, level
        node = response.json()
    too_deep = add(owner_api, "Seviye 6", node)
    assert too_deep.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "beş seviye" in too_deep.json()["fields"]["parent"][0]


def test_the_list_carries_depth_and_counts_for_the_tree(owner_api):
    giyim = add(owner_api, "Giyim").json()
    elbise = add(owner_api, "Elbise", giyim).json()
    add(owner_api, "Etek", giyim)
    add(owner_api, "Midi", elbise)
    product_in("Çiçekli Elbise", Category.objects.get(pk=elbise["id"]))
    rows = {c["name"]: c for c in owner_api.get(f"{ADMIN}/categories").json()}
    assert [rows[n]["depth"] for n in ("Giyim", "Elbise", "Midi")] == [1, 2, 3]
    assert (rows["Giyim"]["child_count"], rows["Giyim"]["product_count"]) == (2, 0)
    assert (rows["Elbise"]["child_count"], rows["Elbise"]["product_count"]) == (1, 1)
    assert (rows["Midi"]["child_count"], rows["Midi"]["product_count"]) == (0, 0)


def test_the_owner_deletes_an_empty_category_and_it_is_audited(owner_api):
    category = add(owner_api, "Eski Bölüm").json()
    profile = CategoryProfile.objects.get(category_id=category["id"])
    profile.image_original = "categories/eski.jpg"
    profile.save()

    response = owner_api.delete(f"{ADMIN}/categories/{category['id']}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert not Category.objects.filter(pk=category["id"]).exists()
    assert [c["name"] for c in owner_api.get(f"{ADMIN}/categories").json()] == []
    assert AuditEntry.objects.filter(summary__contains="Eski Bölüm").count() == 1
    assert PendingFileRemoval.objects.filter(name="categories/eski.jpg").exists()


def test_a_category_with_children_or_products_is_not_deleted(owner_api):
    giyim = add(owner_api, "Giyim").json()
    elbise = add(owner_api, "Elbise", giyim).json()
    add(owner_api, "Etek", giyim)
    product_in("Çiçekli Elbise", Category.objects.get(pk=elbise["id"]))

    parent = owner_api.delete(f"{ADMIN}/categories/{giyim['id']}")
    leaf = owner_api.delete(f"{ADMIN}/categories/{elbise['id']}")

    assert parent.status_code == HTTPStatus.CONFLICT
    assert parent.json()["code"] == "category_in_use"
    assert "2 alt kategori" in parent.json()["message"]
    assert leaf.status_code == HTTPStatus.CONFLICT
    assert "1 ürün" in leaf.json()["message"]
    assert Category.objects.count() == 3
    assert not AuditEntry.objects.filter(summary__contains="silindi").exists()


def move(api, category, parent):
    return api.post(
        f"{ADMIN}/categories/{category['id']}/move",
        {"parent_id": parent["id"] if parent else None},
    )


def test_a_move_carries_the_subtree_and_lands_last_among_the_new_siblings(owner_api):
    giyim = add(owner_api, "Giyim").json()
    elbise = add(owner_api, "Elbise", giyim).json()
    midi = add(owner_api, "Midi", elbise).json()
    aksesuar = add(owner_api, "Aksesuar").json()
    add(owner_api, "Çanta", aksesuar)
    product = product_in("Çiçekli Midi", Category.objects.get(pk=midi["id"]))

    response = move(owner_api, elbise, aksesuar)

    assert response.status_code == HTTPStatus.OK
    rows = {c["name"]: c for c in owner_api.get(f"{ADMIN}/categories").json()}
    assert rows["Elbise"]["parent_id"] == aksesuar["id"]
    assert (rows["Elbise"]["depth"], rows["Midi"]["depth"]) == (2, 3)
    assert rows["Midi"]["parent_id"] == elbise["id"]
    assert rows["Elbise"]["sort_order"] > rows["Çanta"]["sort_order"]
    assert (rows["Giyim"]["child_count"], rows["Aksesuar"]["child_count"]) == (0, 2)
    assert (
        Category.objects.get(pk=midi["id"])
        .productcategory_set.filter(
            product=product,
        )
        .exists()
    )
    assert AuditEntry.objects.filter(summary__contains="Elbise").count() == 1


def test_a_category_can_move_to_the_top_level(owner_api):
    giyim = add(owner_api, "Giyim").json()
    elbise = add(owner_api, "Elbise", giyim).json()

    response = move(owner_api, elbise, None)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["parent_id"] is None
    assert response.json()["depth"] == 1


def chain(api, *names, parent=None):
    nodes = []
    for name in names:
        parent = add(api, name, parent).json()
        nodes.append(parent)
    return nodes


def test_a_category_cannot_move_under_itself_or_its_own_descendants(owner_api):
    giyim, elbise, midi = chain(owner_api, "Giyim", "Elbise", "Midi")

    for target in (giyim, elbise, midi):
        response = move(owner_api, giyim, target)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, target["name"]
        assert "kendi altına" in response.json()["fields"]["parent_id"][0]
    rows = {c["name"]: c for c in owner_api.get(f"{ADMIN}/categories").json()}
    assert (rows["Giyim"]["depth"], rows["Midi"]["depth"]) == (1, 3)
    assert not AuditEntry.objects.filter(summary__contains="taşındı").exists()


def test_a_move_may_not_push_the_deepest_descendant_past_level_five(owner_api):
    *_, level4 = chain(owner_api, "A1", "A2", "A3", "A4")
    pair_top, _ = chain(owner_api, "B1", "B2")
    single = add(owner_api, "C1").json()

    too_deep = move(owner_api, pair_top, level4)  # B1 would be 5, B2 would be 6
    assert too_deep.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "beş seviye" in too_deep.json()["fields"]["parent_id"][0]

    fits = move(owner_api, single, level4)  # C1 lands exactly on level 5
    assert fits.status_code == HTTPStatus.OK
    assert fits.json()["depth"] == 5
    rows = {c["name"]: c for c in owner_api.get(f"{ADMIN}/categories").json()}
    assert (rows["B1"]["depth"], rows["B2"]["depth"]) == (1, 2)


def test_moving_to_the_current_parent_changes_nothing(owner_api):
    giyim, elbise = chain(owner_api, "Giyim", "Elbise")
    add(owner_api, "Etek", giyim)
    before = {
        c["name"]: c["sort_order"] for c in owner_api.get(f"{ADMIN}/categories").json()
    }

    assert move(owner_api, elbise, giyim).status_code == HTTPStatus.OK

    after = {
        c["name"]: c["sort_order"] for c in owner_api.get(f"{ADMIN}/categories").json()
    }
    assert after == before
    assert not AuditEntry.objects.filter(summary__contains="taşındı").exists()


def test_staff_cannot_move_or_delete_but_can_still_reorder(owner_api, api_for):
    giyim, elbise = chain(owner_api, "Giyim", "Elbise")
    etek = add(owner_api, "Etek", giyim).json()
    staff_api = api_for(staff_member("Staff"))

    assert move(staff_api, elbise, None).status_code == HTTPStatus.FORBIDDEN
    assert (
        staff_api.delete(f"{ADMIN}/categories/{etek['id']}").status_code
        == HTTPStatus.FORBIDDEN
    )
    reorder = staff_api.post(
        f"{ADMIN}/categories/reorder",
        {"parent_id": giyim["id"], "ids": [etek["id"], elbise["id"]]},
    )
    assert reorder.status_code == HTTPStatus.OK
    assert Category.objects.filter(pk=etek["id"]).exists()
    assert Category.objects.get(pk=elbise["id"]).depth == 2


# -- the homepage row ------------------------------------------------------------


def _home_names():
    response = Client().get("/api/store/home-categories")
    assert response.status_code == HTTPStatus.OK
    return [c["slug"] for c in response.json()]


def test_only_the_categories_the_owner_picked_show_on_the_homepage_in_order(owner_api):
    first = add(owner_api, "Aaa").json()
    second = add(owner_api, "Bbb").json()
    add(owner_api, "Ccc")

    assert _home_names() == []  # nothing picked: the row is empty
    owner_api.patch(
        f"{ADMIN}/categories/{second['id']}",
        {"show_on_home": True, "home_order": 1},
    )
    owner_api.patch(
        f"{ADMIN}/categories/{first['id']}",
        {"show_on_home": True, "home_order": 2},
    )

    assert _home_names() == [second["slug"], first["slug"]]


def test_a_hidden_category_or_one_under_a_hidden_parent_is_left_out_of_the_row(
    owner_api,
):
    parent = add(owner_api, "Üst").json()
    child = add(owner_api, "Alt", parent).json()
    owner_api.patch(f"{ADMIN}/categories/{child['id']}", {"show_on_home": True})
    assert _home_names() == [child["slug"]]

    owner_api.patch(f"{ADMIN}/categories/{parent['id']}", {"is_hidden": True})

    assert _home_names() == []


def test_the_home_row_settings_are_validated(owner_api):
    made = add(owner_api, "Xyz").json()
    url = f"{ADMIN}/categories/{made['id']}"

    assert (
        owner_api.patch(url, {"home_order": 10000}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        owner_api.patch(url, {"home_order": None}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        owner_api.patch(url, {"show_on_home": None}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert owner_api.patch(url, {"show_on_home": True}).json()["show_on_home"] is True
