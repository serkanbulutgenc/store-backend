# ruff: noqa: PLR2004
from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.services import catalog
from store_backend.services import categories
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin"
STORE = "/api/store"


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


@pytest.fixture
def tree():
    giyim = catalog.create_category("Giyim")
    return {
        "giyim": giyim,
        "elbise": catalog.create_category("Elbise", giyim),
        "etek": catalog.create_category("Etek", giyim),
        "ayakkabi": catalog.create_category("Ayakkabı"),
    }


def product_in(title, *cats):
    product = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cats[0],
        shelf_price="100.00",
    )
    colour = catalog.create_colour(f"Renk {title}", "#112233")
    catalog.create_variant(product, colour=colour, size="M", stock=3)
    for extra in cats[1:]:
        from oscar.apps.catalogue.models import ProductCategory  # noqa: PLC0415

        ProductCategory.objects.create(product=product, category=extra)
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )
    return product


def hide(api, category, *, hidden=True):
    response = api.patch(
        f"{ADMIN}/categories/{category.pk}",
        {"is_hidden": hidden},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def menu(client=None):
    return (client or Client()).get(f"{STORE}/categories").json()


def test_every_category_gets_a_profile(tree):
    assert all(
        CategoryProfile.objects.filter(category=c).exists() for c in tree.values()
    )


def test_staff_can_edit_properties(staff_api, tree):
    response = staff_api.patch(
        f"{ADMIN}/categories/{tree['elbise'].pk}",
        {
            "description": "  Yazlık elbiseler  ",
            "seo_title": "Elbise modelleri",
            "seo_description": "En güzel elbiseler",
        },
    )
    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["description"] == "Yazlık elbiseler"
    assert body["seo_title"] == "Elbise modelleri"
    assert body["is_hidden"] is False
    listed = staff_api.get(f"{ADMIN}/categories").json()
    assert {c["id"]: c["description"] for c in listed}[tree["elbise"].pk] == (
        "Yazlık elbiseler"
    )


def test_property_limits_are_enforced(staff_api, tree):
    url = f"{ADMIN}/categories/{tree['elbise'].pk}"
    for field, size in (("seo_title", 71), ("seo_description", 161)):
        response = staff_api.patch(url, {field: "x" * size})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, field
        assert field in response.json()["fields"]


def test_reordering_siblings_changes_the_menu_without_a_deploy(staff_api, tree):
    assert [n["name"] for n in menu()] == ["Giyim", "Ayakkabı"]  # creation order
    response = staff_api.post(
        f"{ADMIN}/categories/reorder",
        {"parent_id": None, "ids": [tree["ayakkabi"].pk, tree["giyim"].pk]},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    assert [n["name"] for n in menu()] == ["Ayakkabı", "Giyim"]

    staff_api.post(
        f"{ADMIN}/categories/reorder",
        {"parent_id": tree["giyim"].pk, "ids": [tree["etek"].pk, tree["elbise"].pk]},
    )
    giyim = next(n for n in menu() if n["slug"] == "giyim")
    assert [c["name"] for c in giyim["children"]] == ["Etek", "Elbise"]


def test_equal_sort_orders_fall_back_to_the_name(tree):
    CategoryProfile.objects.update(sort_order=0)
    assert [n["name"] for n in menu()] == ["Ayakkabı", "Giyim"]


def test_reorder_needs_exactly_the_sibling_group(staff_api, tree):
    for ids in ([tree["giyim"].pk], [tree["giyim"].pk, tree["elbise"].pk]):
        response = staff_api.post(
            f"{ADMIN}/categories/reorder",
            {"parent_id": None, "ids": ids},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, ids
    assert [n["name"] for n in menu()] == ["Giyim", "Ayakkabı"]


def test_hiding_a_parent_hides_children_everywhere_and_404s(staff_api, tree):
    client = Client()
    product_in("Elbise A", tree["elbise"])
    hide(staff_api, tree["giyim"])

    assert [n["slug"] for n in menu(client)] == ["ayakkabi"]
    for slug in ("giyim", "elbise"):
        assert client.get(f"{STORE}/categories/{slug}").status_code == 404
        assert client.get(f"{STORE}/products", {"category": slug}).status_code == 404
    sitemap = client.get("/sitemap.xml").content.decode()
    assert "/kategori/giyim<" not in sitemap
    assert "/kategori/elbise<" not in sitemap
    assert "/kategori/ayakkabi<" in sitemap

    hide(staff_api, tree["giyim"], hidden=False)
    assert client.get(f"{STORE}/categories/elbise").status_code == 200


def test_hiding_a_child_keeps_the_parent_but_drops_its_products(staff_api, tree):
    client = Client()
    product_in("Elbise A", tree["elbise"])
    product_in("Etek A", tree["etek"])
    hide(staff_api, tree["elbise"])

    giyim = next(n for n in menu(client) if n["slug"] == "giyim")
    assert [c["slug"] for c in giyim["children"]] == ["etek"]
    listed = client.get(f"{STORE}/products", {"category": "giyim"}).json()
    assert [i["title"] for i in listed["items"]] == ["Etek A"]


def test_product_in_hidden_and_visible_category_lists_under_the_visible_one(
    staff_api,
    tree,
):
    client = Client()
    both = product_in("İkisinde", tree["elbise"], tree["ayakkabi"])
    only_hidden = product_in("Gizlide", tree["elbise"])
    hide(staff_api, tree["elbise"])

    listed = client.get(f"{STORE}/products", {"category": "ayakkabi"}).json()
    assert [i["title"] for i in listed["items"]] == ["İkisinde"]
    # Reachable by its own URL, with a visible breadcrumb.
    slug = both.profile.slug
    detail = client.get(f"{STORE}/products/{slug}").json()
    assert [c["slug"] for c in detail["breadcrumbs"]] == ["ayakkabi"]
    assert client.get(f"{STORE}/products/{only_hidden.profile.slug}").status_code == 200


def test_category_page_returns_copy_and_seo_fields(staff_api, tree):
    staff_api.patch(
        f"{ADMIN}/categories/{tree['elbise'].pk}",
        {"description": "Metin", "seo_title": "Başlık", "seo_description": "Özet"},
    )
    page = Client().get(f"{STORE}/categories/elbise").json()
    assert page["description"] == "Metin"
    assert page["seo_title"] == "Başlık"
    assert page["seo_description"] == "Özet"
    assert page["image"] is None
    assert [c["slug"] for c in page["breadcrumbs"]] == ["giyim"]
    giyim = Client().get(f"{STORE}/categories/giyim").json()
    assert [c["slug"] for c in giyim["children"]] == ["elbise", "etek"]


def test_image_shows_with_the_same_derivative_sizes_as_product_photos(
    staff_api,
    tree,
):
    url = f"{ADMIN}/categories/{tree['elbise'].pk}/image"
    response = post_file(staff_api, url, make_file(size=(1800, 1200)))
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["image"]["status"] == "processing"
    assert Client().get(f"{STORE}/categories/elbise").json()["image"] is None

    categories.process_image(tree["elbise"].pk)

    image = Client().get(f"{STORE}/categories/elbise").json()["image"]
    assert [s["width"] for s in image["sizes"]] == [400, 800, 1600]
    assert image["alt"] == "Elbise"
    staff_api.patch(
        f"{ADMIN}/categories/{tree['elbise'].pk}",
        {"image_alt": "Mavi elbise"},
    )
    assert Client().get(f"{STORE}/categories/elbise").json()["image"]["alt"] == (
        "Mavi elbise"
    )


def test_replacing_and_removing_the_image_schedules_old_files_for_removal(
    staff_api,
    tree,
):
    from store_backend.catalog.models import PendingFileRemoval  # noqa: PLC0415

    url = f"{ADMIN}/categories/{tree['elbise'].pk}/image"
    post_file(staff_api, url, make_file(colour=(1, 2, 3)))
    categories.process_image(tree["elbise"].pk)
    first = CategoryProfile.objects.get(category=tree["elbise"]).image_file_names()
    assert first

    post_file(staff_api, url, make_file(colour=(9, 9, 9)))
    assert set(first) <= set(PendingFileRemoval.objects.values_list("name", flat=True))

    response = staff_api.delete(url)
    assert response.status_code == HTTPStatus.OK
    assert response.json()["image"] is None
    assert Client().get(f"{STORE}/categories/elbise").json()["image"] is None


def test_bad_image_is_refused(staff_api, tree):
    url = f"{ADMIN}/categories/{tree['elbise'].pk}/image"
    response = post_file(staff_api, url, make_file(size=(300, 200)))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_admin_category_endpoints_need_sign_in(tree):
    client = Client()
    for path in ("reorder", f"{tree['elbise'].pk}/image"):
        response = client.post(
            f"{ADMIN}/categories/{path}",
            {},
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.UNAUTHORIZED, path
