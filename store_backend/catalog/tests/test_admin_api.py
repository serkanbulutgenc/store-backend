from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

BASE = "/api/admin"
OWNER_FIELDS = {"cost_price", "supplier", "supplier_code"}


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


@pytest.fixture
def seed():
    cat = catalog.create_category("Giyim")
    sub = catalog.create_category("Elbise", parent=cat)
    supplier = catalog.create_supplier("Tedarik A.Ş.", "TED1")
    colour = catalog.create_colour("Siyah", "#000000")
    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=sub,
        shelf_price="199.90",
        supplier=supplier,
        supplier_code="E-100",
        cost_price="90.00",
    )
    variant = catalog.create_variant(product, colour=colour, size="M", stock=5)
    return {
        "cat": cat, "sub": sub, "supplier": supplier, "colour": colour,
        "product": product, "variant": variant,
    }  # fmt: skip


def new_product_body(seed, **extra):
    return {
        "title": "Yeni Elbise",
        "product_class_id": ProductClass.objects.get(name="Giyim").pk,
        "category_id": seed["sub"].pk,
        "shelf_price": "249.90",
        **extra,
    }


# --- acceptance: Staff cannot see cost price or Supplier data ------------------


def test_owner_sees_cost_price_and_supplier(owner_api, seed):
    body = owner_api.get(f"{BASE}/products/{seed['product'].pk}").json()
    assert body["cost_price"] == "90.00"
    assert body["supplier"]["code"] == "TED1"
    assert body["supplier_code"] == "E-100"


def test_staff_response_omits_cost_price_and_supplier_fields(staff_api, seed):
    response = staff_api.get(f"{BASE}/products/{seed['product'].pk}")
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert not OWNER_FIELDS & body.keys()
    assert body["shelf_price"] == "199.90"
    assert body["variants"][0]["sku"].startswith("AUR-")
    assert "90.00" not in response.content.decode()
    assert "TED1" not in response.content.decode()


def test_staff_create_response_omits_owner_fields(staff_api, seed):
    response = staff_api.post(f"{BASE}/products", new_product_body(seed))
    assert response.status_code == HTTPStatus.CREATED
    assert not OWNER_FIELDS & response.json().keys()


def test_staff_cannot_send_owner_fields(staff_api, seed):
    for extra in ({"cost_price": "10.00"}, {"supplier_id": seed["supplier"].pk}):
        response = staff_api.post(f"{BASE}/products", new_product_body(seed, **extra))
        assert response.status_code == HTTPStatus.FORBIDDEN
    response = staff_api.client.patch(
        f"{BASE}/products/{seed['product'].pk}",
        {"cost_price": "1.00"},
        content_type="application/json",
        headers=staff_api.headers,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_staff_cannot_use_supplier_and_colour_management(staff_api, seed):
    assert staff_api.get(f"{BASE}/suppliers").status_code == HTTPStatus.FORBIDDEN
    assert (
        staff_api.post(f"{BASE}/suppliers", {"name": "X", "code": "X"}).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert (
        staff_api.post(
            f"{BASE}/colours",
            {"name": "Mor", "hex_code": "#800080"},
        ).status_code
        == HTTPStatus.FORBIDDEN
    )
    # but Staff can read the colour list to build Variants
    assert staff_api.get(f"{BASE}/colours").status_code == HTTPStatus.OK


# --- archive is Owner-only ------------------------------------------------------


def test_staff_can_publish_but_not_archive_or_unarchive(staff_api, owner_api, seed):
    url = f"{BASE}/products/{seed['product'].pk}/status"
    assert staff_api.post(url, {"status": "published"}).status_code == HTTPStatus.OK
    assert (
        staff_api.post(url, {"status": "archived"}).status_code == HTTPStatus.FORBIDDEN
    )
    assert owner_api.post(url, {"status": "archived"}).status_code == HTTPStatus.OK
    assert staff_api.post(url, {"status": "draft"}).status_code == HTTPStatus.FORBIDDEN
    assert owner_api.post(url, {"status": "draft"}).status_code == HTTPStatus.OK


# --- acceptance: stock adjustments ---------------------------------------------


def test_stock_adjustment_without_reason_is_rejected(staff_api, seed):
    url = f"{BASE}/variants/{seed['variant'].pk}/stock-adjustments"
    for body in ({"delta": 1}, {"delta": 1, "reason": ""}, {"delta": 1, "reason": " "}):
        response = staff_api.post(url, body)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, body
        assert "reason" in response.json()["fields"]
    assert not AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT).exists()


def test_every_stock_adjustment_is_in_the_audit_log(staff_api, seed):
    url = f"{BASE}/variants/{seed['variant'].pk}/stock-adjustments"
    r1 = staff_api.post(url, {"delta": -2, "reason": "Mağazada satıldı"})
    r2 = staff_api.post(url, {"delta": 10, "reason": "Yeni mal geldi"})
    assert r1.status_code == r2.status_code == HTTPStatus.OK
    assert r2.json()["stock"]["on_hand"] == 13  # noqa: PLR2004
    entries = AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT)
    assert entries.count() == 2  # noqa: PLR2004
    assert all(e.actor_email for e in entries)


def test_low_stock_list(staff_api, seed):
    catalog.create_variant(seed["product"], colour=seed["colour"], size="S", stock=1)
    body = staff_api.get(f"{BASE}/stock/low").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert (item["size"], item["on_hand"], item["threshold"]) == ("S", 1, 2)


# --- products ---------------------------------------------------------------------


def test_product_list_paginates_searches_and_filters(staff_api, seed):
    body = staff_api.get(f"{BASE}/products").json()
    assert body["total"] == 1
    assert body["items"][0]["variant_count"] == 1
    assert body["items"][0]["stock_total"] == 5  # noqa: PLR2004
    sku = seed["variant"].variant_profile.sku
    assert staff_api.get(f"{BASE}/products?q={sku}").json()["total"] == 1
    assert staff_api.get(f"{BASE}/products?q=zzz").json()["total"] == 0
    assert staff_api.get(f"{BASE}/products?status=published").json()["total"] == 0
    assert (
        staff_api.get(f"{BASE}/products?ordering=-shelf_price").status_code
        == HTTPStatus.OK
    )
    assert (
        staff_api.get(f"{BASE}/products?ordering=cost_price").status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_create_product_and_variant_via_api(staff_api, seed):
    created = staff_api.post(f"{BASE}/products", new_product_body(seed)).json()
    assert created["slug"] == "yeni-elbise"
    assert created["status"] == ProductStatus.DRAFT
    response = staff_api.post(
        f"{BASE}/products/{created['id']}/variants",
        {"colour_id": seed["colour"].pk, "size": "L", "stock": 3},
    )
    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["stock"] == {"on_hand": 3, "allocated": 0}
    bad = staff_api.post(
        f"{BASE}/products/{created['id']}/variants",
        {"colour_id": seed["colour"].pk, "size": "42"},
    )
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_patch_product_changes_price_and_audits(staff_api, seed):
    response = staff_api.client.patch(
        f"{BASE}/products/{seed['product'].pk}",
        {"shelf_price": "219.90", "old_price": "299.90"},
        content_type="application/json",
        headers=staff_api.headers,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["shelf_price"] == "219.90"
    assert AuditEntry.objects.filter(action=audit.Action.PRICE_CHANGE).count() == 1


def test_owner_can_set_supplier_and_cost_price(owner_api, seed):
    response = owner_api.client.patch(
        f"{BASE}/products/{seed['product'].pk}",
        {"cost_price": "95.50", "supplier_code": "E-200"},
        content_type="application/json",
        headers=owner_api.headers,
    )
    assert response.json()["cost_price"] == "95.50"
    assert response.json()["supplier_code"] == "E-200"


def test_unknown_reference_ids_are_422(staff_api, seed):
    response = staff_api.post(f"{BASE}/products", new_product_body(seed, brand_id=9999))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "brand_id" in response.json()["fields"]


def test_publish_with_no_variant_is_rejected(staff_api, seed):
    created = staff_api.post(f"{BASE}/products", new_product_body(seed)).json()
    response = staff_api.post(
        f"{BASE}/products/{created['id']}/status",
        {"status": "published"},
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_update_variant_sku(staff_api, seed):
    response = staff_api.client.patch(
        f"{BASE}/variants/{seed['variant'].pk}",
        {"sku": "ELB-SIYAH-M", "barcode": "8690000000017"},
        content_type="application/json",
        headers=staff_api.headers,
    )
    assert response.json()["sku"] == "ELB-SIYAH-M"


# --- reference data -----------------------------------------------------------------


def test_owner_manages_colours_and_suppliers(owner_api):
    colour = owner_api.post(f"{BASE}/colours", {"name": "Mor", "hex_code": "#800080"})
    assert colour.status_code == HTTPStatus.CREATED
    assert colour.json()["code"] == "MOR"
    patched = owner_api.client.patch(
        f"{BASE}/colours/{colour.json()['id']}",
        {"is_active": False},
        content_type="application/json",
        headers=owner_api.headers,
    )
    assert patched.json()["is_active"] is False
    supplier = owner_api.post(f"{BASE}/suppliers", {"name": "A", "code": "A1"})
    assert supplier.status_code == HTTPStatus.CREATED
    dup = owner_api.post(f"{BASE}/suppliers", {"name": "B", "code": "a1"})
    assert dup.status_code == HTTPStatus.CONFLICT


def test_staff_manage_categories_and_brands(staff_api, seed):
    top = staff_api.post(f"{BASE}/categories", {"name": "Ayakkabı"}).json()
    child = staff_api.post(
        f"{BASE}/categories",
        {"name": "Sandalet", "parent_id": top["id"]},
    )
    assert child.json()["parent_id"] == top["id"]
    too_deep = staff_api.post(
        f"{BASE}/categories",
        {"name": "Düz", "parent_id": child.json()["id"]},
    )
    assert too_deep.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    listing = staff_api.get(f"{BASE}/categories").json()
    assert {c["name"] for c in listing} >= {"Giyim", "Elbise", "Ayakkabı", "Sandalet"}
    assert (
        staff_api.post(f"{BASE}/brands", {"name": "Marka"}).status_code
        == HTTPStatus.CREATED
    )
    assert staff_api.get(f"{BASE}/product-classes").json()[0]["sizes"]


def test_every_catalog_endpoint_declares_a_permission(client):
    # Anonymous callers get 401, never 403 permission_not_declared (which would mean the
    # endpoint forgot `auth=requires(...)`).
    for path in (
        "/products",
        "/colours",
        "/suppliers",
        "/brands",
        "/categories",
        "/stock/low",
    ):
        response = client.get(f"{BASE}{path}")
        assert response.status_code == HTTPStatus.UNAUTHORIZED, path
