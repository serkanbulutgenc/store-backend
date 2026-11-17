# ruff: noqa: PLR2004
"""The Owner's Menu builder API (docs/adr/0007)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import menu_admin
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import Menu
from store_backend.shop.models import MenuItem
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

HEADER = "/api/admin/menus/header"
FOOTER = "/api/admin/menus/footer"
PUBLIC = "/api/store/menus"


@pytest.fixture
def boss(owner, api_for):  # noqa: F811
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


def group(label="Grup", *children, **fields):
    return {
        "target_type": "group",
        "label": label,
        "children": list(children),
        **fields,
    }


def category(pk, **fields):
    return {"target_type": "category", "category_id": pk, **fields}


def product_item(pk, **fields):
    return {"target_type": "product", "product_id": pk, **fields}


def brand_item(pk, **fields):
    return {"target_type": "brand", "brand_id": pk, **fields}


def make_product(title="Elbise", *, status=ProductStatus.PUBLISHED, brand=None):
    made = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
        brand=brand,
    )
    ProductProfile.objects.filter(product=made).update(status=status)
    return made


def builtin(page, **fields):
    return {"target_type": "builtin", "builtin": page, **fields}


def url(value, label="Bağlantı", **fields):
    return {"target_type": "url", "url": value, "label": label, **fields}


LEGAL_INDEX = builtin("legal_search")


def public():
    return Client().get(PUBLIC).json()


def rule(response):
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.content
    return response.json()["code"]


# --- who may use it ------------------------------------------------------------------


def test_staff_can_neither_read_nor_write_a_menu(staff):
    assert staff.get(HEADER).status_code == HTTPStatus.FORBIDDEN
    saved = staff.put(HEADER, {"items": [builtin("home")]})
    assert saved.status_code == HTTPStatus.FORBIDDEN
    assert not MenuItem.objects.filter(target_type="builtin", builtin="home").exists()


def test_the_owner_reads_and_writes_a_menu(boss):
    assert boss.get(HEADER).status_code == HTTPStatus.OK
    assert boss.put(HEADER, {"items": [builtin("home")]}).status_code == HTTPStatus.OK


def test_an_unknown_menu_is_not_found(boss):
    assert boss.get("/api/admin/menus/sidebar").status_code == HTTPStatus.NOT_FOUND
    saved = boss.put("/api/admin/menus/sidebar", {"items": []})
    assert saved.status_code == HTTPStatus.NOT_FOUND


# --- reading for editing -------------------------------------------------------------


def test_reading_shows_hidden_and_non_public_items_with_the_reason(boss):
    giyim = catalog.create_category("Giyim")
    gizli = catalog.create_category("Gizli")
    gizli.profile.is_hidden = True
    gizli.profile.save()
    saved = boss.put(
        HEADER,
        {
            "items": [
                category(giyim.pk),
                category(gizli.pk),
                group("Eski", builtin("home"), is_hidden=True),
                group("Açık", builtin("basket", label="Çanta")),
            ],
        },
    )
    assert saved.status_code == HTTPStatus.OK, saved.content

    items = boss.get(HEADER).json()["items"]

    assert [(i["target_label"], i["not_shown_reason"]) for i in items[:2]] == [
        ("Giyim", None),
        ("Gizli", "target_not_public"),
    ]
    assert items[2]["not_shown_reason"] == "hidden"
    assert [c["not_shown_reason"] for c in items[2]["children"]] == ["parent_not_shown"]
    shown = items[3]["children"][0]
    assert (shown["label"], shown["target_label"], shown["link"]) == (
        "Çanta",
        "Sepet",
        "/sepet",
    )
    assert shown["not_shown_reason"] is None
    assert boss.get(HEADER).json()["depth_limit"] == 3


# --- saving: add, edit, remove, reorder, re-parent ------------------------------------


def test_a_save_replaces_the_tree_and_the_public_menu_follows_at_once(boss):
    giyim = catalog.create_category("Giyim")
    catalog.create_category("Elbise", giyim)
    first = boss.put(
        HEADER,
        {"items": [category(giyim.pk, include_subcategories=True), builtin("home")]},
    )
    assert first.status_code == HTTPStatus.OK, first.content
    names = [
        (n["label"], [c["label"] for c in n["children"]]) for n in public()["header"]
    ]
    assert names == [("Giyim", ["Elbise"]), ("Ana sayfa", [])]

    # reorder, re-parent, relabel, hide and remove in one save
    second = boss.put(
        HEADER,
        {
            "items": [
                group("Sayfalar", builtin("home", label="Giriş")),
                category(giyim.pk, is_hidden=True),
            ],
        },
    )

    assert second.status_code == HTTPStatus.OK, second.content
    assert [
        (n["label"], [c["label"] for c in n["children"]]) for n in public()["header"]
    ] == [("Sayfalar", ["Giriş"])]
    saved = second.json()["items"]
    assert [i["target_type"] for i in saved] == ["group", "category"]
    assert saved[1]["is_hidden"] is True


def test_duplicates_of_the_same_target_are_allowed(boss):
    giyim = catalog.create_category("Giyim")

    saved = boss.put(HEADER, {"items": [category(giyim.pk), category(giyim.pk)]})

    assert saved.status_code == HTTPStatus.OK, saved.content
    assert MenuItem.objects.filter(category=giyim).count() == 2


def test_include_subcategories_defaults_to_off(boss):
    giyim = catalog.create_category("Giyim")
    catalog.create_category("Elbise", giyim)

    boss.put(HEADER, {"items": [category(giyim.pk)]})

    assert [n["children"] for n in public()["header"]] == [[]]


def test_the_public_output_stops_included_subcategories_at_the_depth_limit(boss):
    giyim = catalog.create_category("Giyim")
    elbise = catalog.create_category("Elbise", giyim)
    midi = catalog.create_category("Midi", elbise)
    catalog.create_category("Yazlık", midi)

    boss.put(HEADER, {"items": [category(giyim.pk, include_subcategories=True)]})

    def depth(nodes):
        return 0 if not nodes else 1 + max(depth(n["children"]) for n in nodes)

    assert depth(public()["header"]) == 3


# --- validation: each rule, each a clear 4xx ----------------------------------------


def test_the_header_nests_three_levels_and_no_more(boss):
    three = group("1", group("2", builtin("home")))
    four = group("1", group("2", group("3", builtin("home"))))

    assert boss.put(HEADER, {"items": [three]}).status_code == HTTPStatus.OK
    refused = boss.put(HEADER, {"items": [four]})

    assert rule(refused) == "menu_too_deep"
    assert "items[0].children[0].children[0].children[0]" in refused.json()["fields"]


def test_the_footer_nests_two_levels_and_no_more(boss):
    two = group("Grup", LEGAL_INDEX)
    three = group("Grup", group("Alt", LEGAL_INDEX))

    assert boss.put(FOOTER, {"items": [two]}).status_code == HTTPStatus.OK
    assert rule(boss.put(FOOTER, {"items": [three]})) == "menu_too_deep"


def test_the_header_can_never_be_saved_empty(boss):
    assert rule(boss.put(HEADER, {"items": []})) == "menu_header_empty"


def test_the_footer_must_link_to_the_legal_page_index(boss):
    assert rule(boss.put(FOOTER, {"items": [builtin("home")]})) == (
        "menu_footer_needs_legal_index"
    )
    assert rule(boss.put(FOOTER, {"items": []})) == "menu_footer_needs_legal_index"
    assert boss.put(FOOTER, {"items": [LEGAL_INDEX]}).status_code == HTTPStatus.OK


def test_a_hidden_legal_index_link_does_not_count(boss):
    hidden = builtin("legal_search", is_hidden=True)
    under_hidden = group("Grup", LEGAL_INDEX, is_hidden=True)

    assert (
        rule(boss.put(FOOTER, {"items": [hidden]})) == "menu_footer_needs_legal_index"
    )
    assert rule(boss.put(FOOTER, {"items": [under_hidden]})) == (
        "menu_footer_needs_legal_index"
    )


def test_any_single_legal_page_may_be_left_out_of_the_footer(boss):
    privacy = {"target_type": "legal_page", "legal_page": "privacy_notice"}

    saved = boss.put(FOOTER, {"items": [LEGAL_INDEX, privacy]})

    assert saved.status_code == HTTPStatus.OK, saved.content
    assert next(i["url"] for i in public()["footer"]) == "/yasal"
    assert len(public()["footer"]) == 2


@pytest.mark.parametrize(
    "value",
    [
        "/blog",
        "/kampanya/yaz?x=1#a",
        "https://instagram.com/aura",
        "https://a.co:8443/x",
    ],
)
def test_a_custom_url_may_be_an_internal_path_or_an_https_address(boss, value):
    saved = boss.put(HEADER, {"items": [url(value)]})

    assert saved.status_code == HTTPStatus.OK, saved.content
    node = public()["header"][0]
    assert (node["url"], node["external"]) == (value, value.startswith("https://"))


@pytest.mark.parametrize(
    "value",
    [
        "http://instagram.com/aura",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>1</script>",
        "vbscript:x",
        "ftp://example.com/x",
        "mailto:a@b.co",
        "//evil.example/x",
        "/\\evil.example",
        "blog",
        "",
        "https://",
        "https:///x",
        "https://exa mple.com",
        "/with space",
        " javascript:alert(1)",
    ],
)
def test_a_custom_url_with_any_other_scheme_or_shape_is_rejected(boss, value):
    refused = boss.put(HEADER, {"items": [builtin("home"), url(value)]})

    assert rule(refused) == "menu_url_invalid"
    assert "items[1].url" in refused.json()["fields"]
    assert not MenuItem.objects.filter(target_type="url").exists()


def test_a_group_heading_carries_no_target(boss):
    for extra in (
        {"builtin": "home"},
        {"url": "/x"},
        {"category_id": catalog.create_category("Giyim").pk},
        {"legal_page": "privacy_notice"},
    ):
        refused = boss.put(HEADER, {"items": [group("Grup", **extra)]})
        assert rule(refused) == "menu_group_has_target"


def test_a_group_heading_needs_a_name(boss):
    assert rule(boss.put(HEADER, {"items": [group("  ")]})) == "menu_group_needs_label"


def test_unknown_target_ids_are_rejected(boss):
    ghost = catalog.create_category("Hayalet")
    missing = ghost.pk
    ghost.delete()

    assert (
        rule(boss.put(HEADER, {"items": [category(missing)]})) == "menu_unknown_target"
    )
    assert (
        rule(boss.put(HEADER, {"items": [builtin("nowhere")]})) == "menu_unknown_target"
    )
    unknown_legal = {"target_type": "legal_page", "legal_page": "nope"}
    assert rule(boss.put(HEADER, {"items": [unknown_legal]})) == "menu_unknown_target"


def test_an_item_must_carry_the_target_its_type_names(boss):
    giyim = catalog.create_category("Giyim")
    for bad in (
        {"target_type": "category"},
        {"target_type": "builtin"},
        {"target_type": "url"},
        {"target_type": "legal_page"},
        {"target_type": "builtin", "builtin": "home", "url": "/x"},
        {"target_type": "builtin", "builtin": "home", "include_subcategories": True},
        {"target_type": "category", "category_id": giyim.pk, "builtin": "home"},
    ):
        assert rule(boss.put(HEADER, {"items": [bad]})) == "menu_target_mismatch"


def test_an_unknown_target_type_is_a_validation_error(boss):
    refused = boss.put(HEADER, {"items": [{"target_type": "product", "label": "x"}]})

    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- atomic, audited ------------------------------------------------------------------


def test_a_failed_save_changes_nothing(boss):
    giyim = catalog.create_category("Giyim")
    boss.put(HEADER, {"items": [category(giyim.pk), builtin("home")]})
    before = list(MenuItem.objects.order_by("pk").values())
    audits = AuditEntry.objects.count()

    refused = boss.put(HEADER, {"items": [builtin("basket"), url("http://x.co")]})

    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert list(MenuItem.objects.order_by("pk").values()) == before
    assert AuditEntry.objects.count() == audits


def test_a_failure_while_writing_rolls_the_whole_save_back(boss, monkeypatch):
    giyim = catalog.create_category("Giyim")
    boss.put(HEADER, {"items": [category(giyim.pk)]})
    before = list(MenuItem.objects.order_by("pk").values())
    audits = AuditEntry.objects.count()
    real = MenuItem.objects.create
    calls = []

    def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            msg = "disk full"
            raise RuntimeError(msg)
        return real(**kwargs)

    monkeypatch.setattr(MenuItem.objects, "create", flaky)
    node = {
        "category_id": None,
        "product_id": None,
        "brand_id": None,
        "collection_id": None,
        "include_subcategories": False,
        "legal_page": None,
        "url": None,
        "label": "",
        "is_hidden": False,
        "children": [],
    }

    with pytest.raises(RuntimeError):
        menu_admin.save(
            None,
            "header",
            [
                {**node, "target_type": "builtin", "builtin": "home"},
                {**node, "target_type": "builtin", "builtin": "basket"},
            ],
        )

    monkeypatch.undo()
    assert list(MenuItem.objects.order_by("pk").values()) == before
    assert AuditEntry.objects.count() == audits


def test_every_save_writes_an_audit_entry_with_before_and_after(boss, owner):  # noqa: F811
    giyim = catalog.create_category("Giyim")
    boss.put(HEADER, {"items": [builtin("home")]})
    boss.put(HEADER, {"items": [category(giyim.pk), builtin("basket", label="Çanta")]})

    entries = AuditEntry.objects.filter(action=audit.Action.MENU_SAVED).order_by("id")

    assert entries.count() == 2
    last = entries.last()
    assert last.actor == owner
    assert last.target_type == "shop.menu"
    assert last.target_id == str(Menu.objects.get(key="header").pk)
    assert [i["builtin"] for i in last.before["items"]] == ["home"]
    assert [i["target_type"] for i in last.after["items"]] == ["category", "builtin"]
    assert last.after["items"][1]["label"] == "Çanta"
    assert "üst menü" in last.summary.lower()


def test_the_audit_entry_for_the_first_save_holds_the_seeded_tree_as_before(boss):
    boss.put(FOOTER, {"items": [LEGAL_INDEX]})

    entry = AuditEntry.objects.get(action=audit.Action.MENU_SAVED)

    assert len(entry.before["items"]) == 9
    assert [i["builtin"] for i in entry.after["items"]] == ["legal_search"]


# --- Product and Brand targets -------------------------------------------------------


def test_the_owner_saves_product_and_brand_items_and_the_public_menu_shows_them(boss):
    aura = catalog.create_brand("Aura")
    elbise = make_product("Elbise", brand=aura)

    saved = boss.put(
        HEADER,
        {"items": [product_item(elbise.pk), brand_item(aura.pk, label="Aura Mağaza")]},
    )

    assert saved.status_code == HTTPStatus.OK, saved.content
    shown = [(n["label"], n["url"]) for n in public()["header"]]
    assert shown == [
        ("Elbise", f"/urun/{elbise.profile.slug}"),
        ("Aura Mağaza", f"/ara?brand={aura.pk}"),
    ]


def test_unknown_product_and_brand_ids_are_rejected(boss):
    aura = catalog.create_brand("Aura")
    missing = aura.pk + 1000

    for bad in (product_item(missing), brand_item(missing)):
        assert rule(boss.put(HEADER, {"items": [bad]})) == "menu_unknown_target"


def a_variant_of(parent):
    return Product.objects.create(structure=Product.CHILD, parent=parent)


def a_product_without_a_page():
    made = make_product("Sayfasız")
    ProductProfile.objects.filter(product=made).delete()
    return Product.objects.get(pk=made.pk)


def test_only_a_product_with_a_page_can_be_a_menu_target(boss):
    elbise = make_product("Elbise")
    variant = a_variant_of(elbise)
    orphan = a_product_without_a_page()

    for bad in (product_item(variant.pk), product_item(orphan.pk)):
        assert rule(boss.put(HEADER, {"items": [bad]})) == "menu_unknown_target"


def test_reading_a_menu_whose_product_lost_its_page_flags_it_instead_of_failing(boss):
    elbise = make_product("Elbise")
    boss.put(HEADER, {"items": [product_item(elbise.pk), builtin("home")]})
    ProductProfile.objects.filter(product=elbise).delete()

    response = boss.get(HEADER)

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["items"][0]["not_shown_reason"] == "target_missing"
    assert [n["label"] for n in public()["header"]] == ["Ana sayfa"]


def test_a_product_item_cannot_also_carry_a_brand(boss):
    aura = catalog.create_brand("Aura")
    elbise = make_product("Elbise", brand=aura)
    both = product_item(elbise.pk, brand_id=aura.pk)

    assert rule(boss.put(HEADER, {"items": [both]})) == "menu_target_mismatch"


def test_reading_flags_a_non_public_product_or_brand_with_the_reason(boss):
    aura = catalog.create_brand("Aura")
    archived = make_product("Eski", brand=aura)
    catalog.archive_product(archived)
    live = make_product("Yeni")
    boss.put(
        HEADER,
        {
            "items": [
                product_item(archived.pk),
                product_item(live.pk),
                brand_item(aura.pk),
            ],
        },
    )

    items = boss.get(HEADER).json()["items"]

    assert [(i["target_label"], i["not_shown_reason"]) for i in items] == [
        ("Eski", "target_not_public"),
        ("Yeni", None),
        ("Aura", "target_not_public"),
    ]
    assert [n["label"] for n in public()["header"]] == ["Yeni"]


# --- the target picker ---------------------------------------------------------------

TARGETS = "/api/admin/menu-targets"


def picked(client, q):
    response = client.get(f"{TARGETS}?q={q}")
    assert response.status_code == HTTPStatus.OK, response.content
    return [
        (
            r["target_type"],
            r["target_label"],
            r[
                {
                    "category": "category_id",
                    "product": "product_id",
                    "brand": "brand_id",
                    "collection": "collection_id",
                    "legal_page": "legal_page",
                }[r["target_type"]]
            ],
        )
        for r in response.json()
    ]


def test_the_picker_finds_categories_products_brands_and_legal_pages_by_name(boss):
    giyim = catalog.create_category("Aura Giyim")
    aura = catalog.create_brand("Aura Home")
    elbise = make_product("Aura Elbise", brand=aura)
    catalog.create_category("Ayakkabı")

    found = picked(boss, "aura")

    assert ("category", "Aura Giyim", giyim.pk) in found
    assert ("brand", "Aura Home", aura.pk) in found
    assert ("product", "Aura Elbise", elbise.pk) in found
    assert not any(label == "Ayakkabı" for _, label, _ in found)

    legal = picked(boss, "gizlilik")
    assert [t for t, _, _ in legal] == ["legal_page"]
    assert legal[0][2] in LEGAL_PAGES


def test_a_picker_result_can_be_saved_as_a_menu_item_as_it_is(boss):
    aura = catalog.create_brand("Aura Home")
    make_product("Aura Elbise", brand=aura)
    result = next(
        r for r in boss.get(f"{TARGETS}?q=aura").json() if r["target_type"] == "brand"
    )

    saved = boss.put(HEADER, {"items": [{**result}]})

    assert saved.status_code == HTTPStatus.OK, saved.content
    assert (
        MenuItem.objects.get(menu__key="header").label == ""
    )  # still follows the Brand's own name


def test_staff_cannot_use_the_picker(staff):
    assert staff.get(f"{TARGETS}?q=a").status_code == HTTPStatus.FORBIDDEN


def test_the_picker_ignores_case_and_turkish_diacritics_for_every_target_type(boss):
    ipek = catalog.create_category("İpek Giyim")
    sik = catalog.create_brand("Şık Home")
    canta = make_product("Çanta", brand=sik)

    assert ("category", "İpek Giyim", ipek.pk) in picked(boss, "ipek")
    assert ("brand", "Şık Home", sik.pk) in picked(boss, "SIK")
    assert ("product", "Çanta", canta.pk) in picked(boss, "canta")
    assert ("product", "Çanta", canta.pk) in picked(boss, "ÇANTA")


# --- Collection targets --------------------------------------------------------------


def collection_item(pk, **fields):
    return {"target_type": "collection", "collection_id": pk, **fields}


def make_collection(name="Editörün seçimi", *products, status="published"):
    collection = Collection.objects.create(
        name=name,
        slug=name.lower().replace(" ", "-").replace("ö", "o").replace("ü", "u"),
        status=status,
    )
    for position, product in enumerate(products):
        CollectionProduct.objects.create(
            collection=collection,
            product=product,
            position=position,
        )
    return collection


def test_the_owner_saves_a_collection_item_and_the_public_menu_shows_it(boss):
    collection = make_collection("Sonbahar", make_product("Elbise"))

    saved = boss.put(
        HEADER,
        {"items": [collection_item(collection.pk, label="Sonbahar Seçkisi")]},
    )

    assert saved.status_code == HTTPStatus.OK, saved.content
    assert saved.json()["items"][0]["collection_id"] == collection.pk
    assert saved.json()["items"][0]["link"] == "/collections/sonbahar"
    assert [(i["label"], i["url"]) for i in public()["header"]] == [
        ("Sonbahar Seçkisi", "/collections/sonbahar"),
    ]


def test_an_unknown_collection_id_is_rejected(boss):
    missing = Collection.objects.count() + 999

    saved = boss.put(HEADER, {"items": [collection_item(missing)]})

    assert rule(saved) == "menu_unknown_target"


def test_a_collection_item_cannot_also_carry_another_target(boss):
    collection = make_collection("Sonbahar", make_product("Elbise"))
    both = collection_item(collection.pk, category_id=catalog.create_category("X").pk)

    assert rule(boss.put(HEADER, {"items": [both]})) == "menu_target_mismatch"
    assert rule(boss.put(HEADER, {"items": [collection_item(None)]})) == (
        "menu_target_mismatch"
    )


@pytest.mark.parametrize("status", ["draft", "unpublished"])
def test_reading_flags_a_collection_that_is_not_published(boss, status):
    collection = make_collection("Sonbahar", make_product("Elbise"), status=status)
    boss.put(HEADER, {"items": [collection_item(collection.pk), LEGAL_INDEX]})

    (flagged, _) = boss.get(HEADER).json()["items"]

    assert flagged["not_shown_reason"] == "target_not_public"
    assert flagged["target_label"] == "Sonbahar"
    assert "Sonbahar" not in [i["label"] for i in public()["header"]]


def test_reading_flags_a_collection_without_visible_products(boss):
    empty = make_collection("Bos")
    hidden = make_collection(
        "Gizli urunler",
        make_product("Eski", status=ProductStatus.ARCHIVED),
    )
    boss.put(
        HEADER,
        {"items": [collection_item(empty.pk), collection_item(hidden.pk)]},
    )

    items = boss.get(HEADER).json()["items"]

    assert [i["not_shown_reason"] for i in items] == ["target_not_public"] * 2


def test_the_picker_finds_collections_by_name(boss):
    collection = make_collection("Sonbahar Seçkisi", make_product("Elbise"))
    draft = make_collection("Sonbahar Taslak", status=CollectionStatus.DRAFT)

    found = picked(boss, "sonbahar")

    assert ("collection", "Sonbahar Seçkisi", collection.pk) in found
    assert ("collection", "Sonbahar Taslak", draft.pk) in found


def test_a_collection_in_a_menu_cannot_be_deleted_until_its_item_is_removed(boss):
    collection = make_collection("Sonbahar", make_product("Elbise"))
    boss.put(HEADER, {"items": [collection_item(collection.pk)]})
    boss.put(
        FOOTER,
        {"items": [collection_item(collection.pk), LEGAL_INDEX]},
    )

    refused = boss.delete(f"/api/admin/collections/{collection.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "collection_in_menu"
    assert "2 menü öğesi" in refused.json()["message"]
    assert Collection.objects.filter(pk=collection.pk).exists()
    assert not AuditEntry.objects.filter(action="collection_deleted").exists()

    boss.put(HEADER, {"items": [builtin("home")]})
    boss.put(FOOTER, {"items": [LEGAL_INDEX]})

    assert boss.delete(f"/api/admin/collections/{collection.pk}").status_code == (
        HTTPStatus.NO_CONTENT
    )
    assert not Collection.objects.filter(pk=collection.pk).exists()
