"""Menus: the Header and Footer menus the Storefront reads (docs/adr/0007)."""

from http import HTTPStatus
from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import ConflictError
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import legal_pages
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import Menu
from store_backend.shop.models import MenuItem
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

MENUS = "/api/store/menus"


def menus():
    response = Client().get(MENUS)
    assert response.status_code == HTTPStatus.OK
    return response.json()


def reseed():
    """Run the data migration's seed against the Categories the test has made."""
    MenuItem.objects.all().delete()
    import_module("store_backend.shop.migrations.0014_menus").seed(apps, None)


def tree(nodes):
    """A menu as nested `(label, url, children)`, to compare with a literal."""
    return [(n["label"], n["url"], tree(n["children"])) for n in nodes]


def item(menu, target_type, parent=None, position=0, **fields):
    return MenuItem.objects.create(
        menu=Menu.objects.get(key=menu),
        parent=parent,
        position=position,
        target_type=target_type,
        **fields,
    )


def clear(menu):
    MenuItem.objects.filter(menu__key=menu).delete()


def product(title="Elbise", *, status=ProductStatus.PUBLISHED, brand=None):
    made = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
        brand=brand,
    )
    ProductProfile.objects.filter(product=made).update(status=status)
    made.refresh_from_db()
    return made


# --- the seed ----------------------------------------------------------------------


def test_the_footer_starts_with_every_legal_page_search_and_order_tracking():
    expected = [
        {"label": v.title, "url": f"/yasal/{legal_pages.slug_of(v.page)}"}
        for v in legal_pages.current_all()
    ]
    expected += [
        {"label": "Yasal sayfalarda ara", "url": "/yasal"},
        {"label": "Sipariş takibi", "url": "/siparis-takip"},
    ]

    footer = menus()["footer"]

    assert [{"label": i["label"], "url": i["url"]} for i in footer] == expected
    assert len(expected) == len(LEGAL_PAGES) + 2


def test_the_header_starts_as_the_category_tree_down_to_three_levels():
    giyim = catalog.create_category("Giyim")
    elbise = catalog.create_category("Elbise", giyim)
    midi = catalog.create_category("Midi", elbise)
    catalog.create_category("Yazlık", midi)  # a fourth level: beyond the Header's limit
    catalog.create_category("Etek", giyim)
    catalog.create_category("Ev")

    reseed()

    assert tree(menus()["header"]) == [
        (
            "Giyim",
            "/kategori/giyim",
            [
                ("Elbise", "/kategori/elbise", [("Midi", "/kategori/midi", [])]),
                ("Etek", "/kategori/etek", []),
            ],
        ),
        ("Ev", "/kategori/ev", []),
    ]


# --- resolving by reference ---------------------------------------------------------


def test_renaming_a_category_or_its_slug_changes_the_menu_without_touching_it():
    giyim = catalog.create_category("Giyim")
    reseed()
    before = list(MenuItem.objects.values_list("pk", "label", "url"))

    giyim.name = "Kadın Giyim"
    giyim.slug = "kadin-giyim"
    giyim.save()

    assert tree(menus()["header"]) == [("Kadın Giyim", "/kategori/kadin-giyim", [])]
    assert list(MenuItem.objects.values_list("pk", "label", "url")) == before


def test_an_owner_label_overrides_the_targets_own_label():
    giyim = catalog.create_category("Giyim")
    clear("header")
    item("header", "category", category=giyim, label="Kadın")

    assert tree(menus()["header"]) == [("Kadın", "/kategori/giyim", [])]


def test_a_category_item_shows_its_categorys_image():
    giyim = catalog.create_category("Giyim")
    profile = giyim.profile
    profile.image_original = "giyim.jpg"
    profile.image_status = "ready"
    profile.image_derivatives = [
        {"width": 200, "name": "giyim-200.jpg"},
        {"width": 800, "name": "giyim-800.jpg"},
    ]
    profile.save()
    clear("header")
    item("header", "category", category=giyim)
    item("header", "group", position=1, label="Bilgi")

    header = menus()["header"]

    assert header[0]["image_url"].endswith("giyim-800.jpg")
    assert header[1]["image_url"] is None


# --- what is left out ---------------------------------------------------------------


def test_a_hidden_item_is_left_out():
    giyim = catalog.create_category("Giyim")
    ev = catalog.create_category("Ev")
    clear("header")
    item("header", "category", category=giyim, is_hidden=True)
    item("header", "category", category=ev, position=1)

    assert tree(menus()["header"]) == [("Ev", "/kategori/ev", [])]


def test_a_hidden_group_heading_hides_its_children_too():
    giyim = catalog.create_category("Giyim")
    clear("footer")
    group = item("footer", "group", label="Kurumsal", is_hidden=True)
    item("footer", "category", parent=group, category=giyim)

    assert menus()["footer"] == []


def test_an_item_whose_category_is_not_public_is_left_out():
    giyim = catalog.create_category("Giyim")
    gizli = catalog.create_category("Gizli")
    altta = catalog.create_category("Altta", gizli)
    gizli.profile.is_hidden = True
    gizli.profile.save()
    clear("header")
    item("header", "category", category=giyim)
    item("header", "category", category=gizli, position=1, include_subcategories=True)
    item("header", "category", category=altta, position=2)

    assert tree(menus()["header"]) == [("Giyim", "/kategori/giyim", [])]


def test_subcategories_included_automatically_skip_a_hidden_one():
    giyim = catalog.create_category("Giyim")
    catalog.create_category("Elbise", giyim)
    gizli = catalog.create_category("Gizli", giyim)
    gizli.profile.is_hidden = True
    gizli.profile.save()
    reseed()

    assert tree(menus()["header"]) == [
        ("Giyim", "/kategori/giyim", [("Elbise", "/kategori/elbise", [])]),
    ]


# --- groups, custom URLs and depth ------------------------------------------------


def test_a_group_heading_links_nowhere_and_holds_its_children_in_order():
    clear("footer")
    group = item("footer", "group", label="Kurumsal")
    item("footer", "builtin", parent=group, position=1, builtin="basket")
    item("footer", "builtin", parent=group, position=0, builtin="home")

    assert tree(menus()["footer"]) == [
        ("Kurumsal", None, [("Ana sayfa", "/", []), ("Sepet", "/sepet", [])]),
    ]


def test_an_external_custom_url_is_flagged_and_an_internal_one_is_not():
    clear("footer")
    item("footer", "url", label="Blog", url="/blog")
    item("footer", "url", position=1, label="Instagram", url="https://instagram.com/x")

    footer = menus()["footer"]

    assert [(i["url"], i["external"]) for i in footer] == [
        ("/blog", False),
        ("https://instagram.com/x", True),
    ]


def test_levels_beyond_a_menus_depth_limit_are_never_returned():
    clear("footer")
    top = item("footer", "group", label="Üst")
    middle = item("footer", "group", parent=top, label="Orta")
    item("footer", "builtin", parent=middle, builtin="home")
    clear("header")
    one = item("header", "group", label="1")
    two = item("header", "group", parent=one, label="2")
    three = item("header", "group", parent=two, label="3")
    item("header", "group", parent=three, label="4")

    result = menus()

    assert tree(result["footer"]) == [("Üst", None, [("Orta", None, [])])]
    assert tree(result["header"]) == [
        ("1", None, [("2", None, [("3", None, [])])]),
    ]


# --- a target cannot be deleted while a Menu item points at it (ADR 0007) -----------


def test_a_category_in_a_menu_cannot_be_deleted_until_its_item_is_removed(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    giyim = catalog.create_category("Giyim")
    clear("header")
    item("header", "category", category=giyim)
    item("footer", "category", category=giyim)

    refused = owner_api.delete(f"/api/admin/categories/{giyim.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "category_in_menu"
    assert "2 menü öğesi" in refused.json()["message"]
    MenuItem.objects.filter(category=giyim).delete()
    assert owner_api.delete(f"/api/admin/categories/{giyim.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )


# --- Product and Brand targets -------------------------------------------------------


def test_a_product_item_links_to_the_product_and_follows_its_title_and_slug():
    elbise = product("Elbise")
    clear("header")
    item("header", "product", product=elbise)

    assert tree(menus()["header"]) == [("Elbise", f"/urun/{elbise.profile.slug}", [])]

    elbise.title = "Midi Elbise"
    elbise.save()
    ProductProfile.objects.filter(product=elbise).update(slug="midi-elbise")

    assert tree(menus()["header"]) == [("Midi Elbise", "/urun/midi-elbise", [])]


def test_archiving_a_product_removes_it_from_the_menu_without_editing_the_menu():
    elbise = product("Elbise")
    clear("header")
    item("header", "product", product=elbise)
    item("header", "builtin", position=1, builtin="home")
    before = list(MenuItem.objects.values_list("pk", flat=True))

    catalog.archive_product(elbise)

    assert [n["label"] for n in menus()["header"]] == ["Ana sayfa"]
    assert list(MenuItem.objects.values_list("pk", flat=True)) == before


def test_a_draft_product_is_left_out_of_the_menu():
    draft = product("Taslak", status=ProductStatus.DRAFT)
    clear("header")
    item("header", "product", product=draft)
    item("header", "builtin", position=1, builtin="home")

    assert [n["label"] for n in menus()["header"]] == ["Ana sayfa"]


def test_a_brand_item_links_to_the_listing_filtered_by_brand_and_follows_its_name():
    aura = catalog.create_brand("Aura")
    product("Elbise", brand=aura)
    clear("header")
    item("header", "brand", brand=aura)

    assert tree(menus()["header"]) == [("Aura", f"/ara?brand={aura.pk}", [])]

    aura.name = "Aura Home"
    aura.save()

    assert tree(menus()["header"]) == [("Aura Home", f"/ara?brand={aura.pk}", [])]


def test_a_brand_without_a_published_product_is_left_out_of_the_menu():
    aura = catalog.create_brand("Aura")
    elbise = product("Elbise", brand=aura)
    clear("header")
    item("header", "brand", brand=aura)
    item("header", "builtin", position=1, builtin="home")
    assert [n["label"] for n in menus()["header"]] == ["Aura", "Ana sayfa"]

    catalog.archive_product(elbise)

    assert [n["label"] for n in menus()["header"]] == ["Ana sayfa"]


# --- Product and Brand deletes are refused while a Menu item points at them ---------


def test_a_brand_in_a_menu_cannot_be_deleted_until_its_item_is_removed(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    aura = catalog.create_brand("Aura")
    item("header", "brand", brand=aura)
    item("footer", "brand", brand=aura)

    refused = owner_api.delete(f"/api/admin/brands/{aura.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "brand_in_menu"
    assert "2 menü öğesi" in refused.json()["message"]
    MenuItem.objects.filter(brand=aura).delete()
    assert owner_api.delete(f"/api/admin/brands/{aura.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )


def test_a_product_in_a_menu_cannot_be_deleted_until_its_item_is_removed(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    elbise = product("Elbise")
    item("header", "product", product=elbise)

    refused = owner_api.delete(f"/api/admin/products/{elbise.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "product_in_menu"
    assert "1 menü öğesi" in refused.json()["message"]
    MenuItem.objects.filter(product=elbise).delete()
    assert owner_api.delete(f"/api/admin/products/{elbise.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )


def test_unreferenced_targets_still_delete(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    aura = catalog.create_brand("Aura")
    elbise = product("Elbise")
    item("header", "brand", brand=catalog.create_brand("Başka"))

    assert owner_api.delete(f"/api/admin/brands/{aura.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )
    assert owner_api.delete(f"/api/admin/products/{elbise.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )


def test_the_menu_item_count_is_in_the_payloads_the_delete_dialog_reads(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    aura = catalog.create_brand("Aura")
    elbise = product("Elbise")
    giyim = catalog.create_category("Giyim")
    unused = catalog.create_brand("Kullanılmayan")
    clear("header")
    for _ in range(2):
        item("header", "brand", brand=aura)
    item("header", "product", product=elbise)
    item("header", "category", category=giyim)

    brands = {b["id"]: b for b in owner_api.get("/api/admin/brands").json()}
    assert brands[aura.pk]["menu_item_count"] == 2  # noqa: PLR2004
    assert brands[unused.pk]["menu_item_count"] == 0
    assert (
        owner_api.get(f"/api/admin/products/{elbise.pk}").json()["menu_item_count"] == 1
    )
    cats = {c["id"]: c for c in owner_api.get("/api/admin/categories").json()}
    assert cats[giyim.pk]["menu_item_count"] == 1


def test_delete_product_itself_refuses_a_product_in_a_menu():
    elbise = product("Elbise")
    item("header", "product", product=elbise)

    with pytest.raises(ConflictError) as refused:
        catalog.delete_product(elbise)

    assert refused.value.code == "product_in_menu"


def test_removing_the_menu_item_lets_the_category_delete_succeed(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    giyim = catalog.create_category("Giyim")
    item("header", "category", category=giyim)

    assert owner_api.delete(f"/api/admin/categories/{giyim.pk}").status_code == (
        HTTPStatus.CONFLICT
    )
    MenuItem.objects.filter(category=giyim).delete()
    assert owner_api.delete(f"/api/admin/categories/{giyim.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )


def test_the_menu_item_counts_cost_one_query_per_list(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    for n in range(4):
        item("header", "brand", brand=catalog.create_brand(f"Marka {n}"))
        item("header", "category", category=catalog.create_category(f"Kat {n}"))

    for path in ("/api/admin/brands", "/api/admin/categories"):
        with CaptureQueriesContext(connection) as ctx:
            assert owner_api.get(path).status_code == HTTPStatus.OK
        menu_queries = [q for q in ctx.captured_queries if "shop_menuitem" in q["sql"]]
        assert len(menu_queries) == 1, path


def test_the_products_list_carries_each_products_menu_item_count(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    elbise = product("Elbise")
    gomlek = product("Gömlek")
    unused = product("Kullanılmayan")
    clear("header")
    for _ in range(2):
        item("header", "product", product=elbise)
    item("header", "product", product=gomlek)

    rows = {p["id"]: p for p in owner_api.get("/api/admin/products").json()["items"]}

    assert rows[elbise.pk]["menu_item_count"] == 2  # noqa: PLR2004
    assert rows[gomlek.pk]["menu_item_count"] == 1
    assert rows[unused.pk]["menu_item_count"] == 0
    detail = owner_api.get(f"/api/admin/products/{elbise.pk}").json()
    assert rows[elbise.pk]["menu_item_count"] == detail["menu_item_count"]


def test_the_products_list_counts_menu_items_once_per_product_when_searching(
    api_for,  # noqa: F811
):
    owner_api = api_for(staff_member("Owner"), enrol=True)
    elbise = product("Elbise")
    clear("header")
    for _ in range(3):
        item("header", "product", product=elbise)

    rows = owner_api.get("/api/admin/products?q=Elbise").json()["items"]

    assert [(r["id"], r["menu_item_count"]) for r in rows] == [(elbise.pk, 3)]


def test_the_products_list_menu_item_count_costs_one_query_per_list(api_for):  # noqa: F811
    owner_api = api_for(staff_member("Owner"), enrol=True)
    for n in range(4):
        item("header", "product", product=product(f"Ürün {n}"))

    with CaptureQueriesContext(connection) as ctx:
        assert owner_api.get("/api/admin/products").status_code == HTTPStatus.OK

    menu_queries = [q for q in ctx.captured_queries if "shop_menuitem" in q["sql"]]
    assert len(menu_queries) == 1
