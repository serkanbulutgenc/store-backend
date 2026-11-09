from __future__ import annotations

from decimal import Decimal

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import stock
from store_backend.staff.models import AuditEntry
from store_backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
D = Decimal
FOUR = 4


@pytest.fixture
def actor():
    return UserFactory.create(is_staff=True)


@pytest.fixture
def product():
    cat = catalog.create_category("Giyim")
    return catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cat,
        shelf_price="100.00",
    )


@pytest.fixture
def colour():
    return catalog.create_colour("Siyah", "#000000")


@pytest.fixture
def variant(product, colour):
    return catalog.create_variant(product, colour=colour, size="M", stock=4)


# --- stock adjustments -------------------------------------------------------


def test_adjustment_changes_stock_and_is_audited(actor, variant):
    record = stock.adjust(actor, variant, delta=-3, reason="Mağazada satıldı")
    assert record.num_in_stock == 1
    entry = AuditEntry.objects.get(action=audit.Action.STOCK_ADJUSTMENT)
    assert entry.actor == actor
    assert entry.before == {"num_in_stock": 4}
    assert entry.after is not None
    assert entry.after["num_in_stock"] == 1
    assert "Mağazada satıldı" in entry.summary


@pytest.mark.parametrize("reason", ["", "  ", "ab"])
def test_adjustment_without_a_reason_is_rejected_and_not_audited(
    actor,
    variant,
    reason,
):
    with pytest.raises(UnprocessableError) as exc:
        stock.adjust(actor, variant, delta=1, reason=reason)
    assert "reason" in exc.value.fields
    assert variant.stockrecords.get().num_in_stock == FOUR
    assert not AuditEntry.objects.exists()


def test_adjustment_cannot_go_below_zero_or_be_zero(actor, variant):
    with pytest.raises(UnprocessableError):
        stock.adjust(actor, variant, delta=-5, reason="sayım")
    with pytest.raises(UnprocessableError):
        stock.adjust(actor, variant, delta=0, reason="sayım")


def test_low_stock_list_uses_each_products_threshold(actor, product, colour, variant):
    other = catalog.create_variant(product, colour=colour, size="L", stock=2)
    assert list(stock.low_stock_variants()) == [other]  # 2 <= default 2; 4 is fine
    catalog.update_product(actor, product, low_stock_threshold=FOUR)
    assert set(stock.low_stock_variants()) == {variant, other}
    catalog.update_product(actor, product, low_stock_threshold=0)
    assert not stock.low_stock_variants()


def test_low_stock_skips_archived_products(product, colour):
    catalog.create_variant(product, colour=colour, size="S", stock=0)
    catalog.archive_product(product)
    assert not stock.low_stock_variants()


# --- product updates ---------------------------------------------------------


def test_price_change_is_audited(actor, product):
    catalog.update_product(actor, product, shelf_price="120.00", old_price="150.00")
    entry = AuditEntry.objects.get(action=audit.Action.PRICE_CHANGE)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["shelf_price"] == "100.00"
    assert entry.after["shelf_price"] == "120.00"
    assert entry.after["old_price"] == "150.00"


def test_unchanged_prices_write_no_audit_entry(actor, product):
    catalog.update_product(actor, product, title="Yeni ad")
    product.refresh_from_db()
    assert product.title == "Yeni ad"
    assert not AuditEntry.objects.exists()


def test_update_can_clear_old_price_and_validates(actor, product):
    catalog.update_product(actor, product, old_price="150.00")
    catalog.update_product(actor, product, old_price=None)
    assert product.profile.old_price is None
    with pytest.raises(UnprocessableError):
        catalog.update_product(actor, product, old_price="50.00")
    with pytest.raises(UnprocessableError):
        catalog.update_product(actor, product, bogus=1)


def test_update_slug_records_redirect(actor, product):
    catalog.update_product(actor, product, slug="yeni-elbise")
    assert catalog.resolve_slug("elbise") == (product, True)


def test_update_variant_sku_syncs_stock_record_and_checks_unique(
    actor,
    product,
    colour,
    variant,
):
    other = catalog.create_variant(product, colour=colour, size="L")
    catalog.update_variant(actor, variant, sku="ELB-1")
    variant.refresh_from_db()
    assert variant.upc == "ELB-1"
    assert variant.stockrecords.get().partner_sku == "ELB-1"
    with pytest.raises(ConflictError):
        catalog.update_variant(actor, other, sku="ELB-1")


def test_variant_price_override_change_is_audited(actor, variant):
    catalog.update_variant(actor, variant, price_override="90.00")
    entry = AuditEntry.objects.get(action=audit.Action.PRICE_CHANGE)
    assert entry.after == {"price_override": "90.00"}


# --- publish hook -------------------------------------------------------------


def test_publish_checks_can_veto_publishing(product, variant):
    def need_photo(p):
        msg = "Fotoğraf ekleyin."
        raise UnprocessableError(msg)

    catalog.PUBLISH_CHECKS.append(need_photo)
    try:
        with pytest.raises(UnprocessableError):
            catalog.set_status(product, ProductStatus.PUBLISHED)
        catalog.set_status(product, ProductStatus.ARCHIVED)  # only publishing is gated
    finally:
        catalog.PUBLISH_CHECKS.remove(need_photo)


# --- catalog data -------------------------------------------------------------


def test_brand_supplier_colour_rules():
    brand = catalog.create_brand("Marka")
    with pytest.raises(ConflictError):
        catalog.create_brand("marka")
    catalog.update_brand(brand, name="Marka 2")
    supplier = catalog.create_supplier("Tedarik A.Ş.", "TED1")
    with pytest.raises(ConflictError):
        catalog.create_supplier("Başka", "ted1")
    catalog.update_supplier(supplier, code="TED2")
    colour = catalog.create_colour("Kırmızı", "#ff0000")
    code = colour.code
    catalog.update_colour(colour, name="Koyu Kırmızı", is_active=False)
    assert colour.code == code
    assert not colour.is_active
    with pytest.raises(UnprocessableError):
        catalog.update_colour(colour, hex_code="red")
