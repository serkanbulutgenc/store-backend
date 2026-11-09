from __future__ import annotations

from decimal import Decimal

import pytest
from oscar.apps.catalogue.models import ProductClass
from oscar.core.loading import get_class

from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.conftest import add_ready_photo
from store_backend.services import catalog
from store_backend.services import pricing

Selector = get_class("partner.strategy", "Selector")
D = Decimal
KURUS = D("0.01")
PRICES = [
    "0.01", "0.99", "1.00", "1.10", "9.99", "19.90", "29.95", "49.99", "99.00",
    "119.99", "129.50", "199.90", "249.00", "299.95", "349.90", "399.99",
    "459.90", "1000.00", "1234.56", "9999.99",
]  # fmt: skip
RATES = [D(0), D(1), D(10), D(20)]


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("gross", PRICES)
def test_split_round_trips_without_kurus_drift(gross, rate):
    gross = D(gross)
    excl, kdv = pricing.split_kdv(gross, rate)
    assert excl + kdv == gross
    assert excl == excl.quantize(KURUS)
    assert kdv == kdv.quantize(KURUS)
    # Within half a kurus of the exact value.
    assert abs(kdv - gross * rate / (100 + rate)) <= D("0.005")


def test_split_rounds_half_up():
    # 10.50 / 1.1 = 9.5454... -> 9.55; kdv takes the remainder.
    assert pricing.split_kdv(D("10.50"), D(10)) == (D("9.55"), D("0.95"))
    assert pricing.split_kdv(D("0.05"), D(20)) == (D("0.04"), D("0.01"))


def test_line_snapshot_rounds_each_line_once():
    line = pricing.line_snapshot(unit_price=D("349.90"), quantity=3, kdv_rate=D(10))
    assert line.unit_price_incl == D("349.90")
    assert line.total_incl == D("1049.70")
    assert line.total_excl + line.total_kdv == line.total_incl
    assert (line.total_excl, line.total_kdv) == pricing.split_kdv(D("1049.70"), D(10))


def test_order_totals_equal_the_sum_of_rounded_lines():
    lines = [
        pricing.line_snapshot(unit_price=D("19.90"), quantity=3, kdv_rate=D(10)),
        pricing.line_snapshot(unit_price=D("129.50"), quantity=1, kdv_rate=D(10)),
        pricing.line_snapshot(unit_price=D("33.33"), quantity=7, kdv_rate=D(20)),
        pricing.fee_line(D("49.90"), kdv_rate=D(20)),
        pricing.fee_line(D("15.00"), kdv_rate=D(20)),
    ]
    totals = pricing.order_totals(lines)
    assert totals.total_incl == sum(line.total_incl for line in lines)
    assert totals.total_excl == sum(line.total_excl for line in lines)
    assert totals.total_kdv == sum(line.total_kdv for line in lines)
    assert totals.total_excl + totals.total_kdv == totals.total_incl
    # Per-rate breakdown for the accountant.
    assert set(totals.by_rate) == {D(10), D(20)}
    assert sum(v.kdv for v in totals.by_rate.values()) == totals.total_kdv


def test_fee_line_uses_the_configured_rate(settings):
    settings.FEE_KDV_RATE = D(18)
    line = pricing.fee_line(D("59.00"))
    assert line.kdv_rate == D(18)
    assert line.quantity == 1
    assert line.total_incl == D("59.00")


def test_quantity_must_be_positive():
    with pytest.raises(ValueError, match="quantity"):
        pricing.line_snapshot(unit_price=D("1.00"), quantity=0, kdv_rate=D(10))


# --- Oscar strategy ----------------------------------------------------------


@pytest.fixture
def variants(db):
    cat = catalog.create_category("Giyim")
    colour = catalog.create_colour("Mavi", "#0000FF")
    product = catalog.create_product(
        title="Gömlek",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cat,
        shelf_price="349.90",
    )
    base = catalog.create_variant(product, colour=colour, size="M", stock=3)
    override = catalog.create_variant(
        product,
        colour=colour,
        size="L",
        stock=3,
        price_override="299.90",
    )
    return product, base, override


def test_selector_resolves_to_the_kdv_strategy():
    assert type(Selector().strategy()).__name__ == "KdvInclusiveStrategy"


def test_strategy_returns_the_shelf_price_with_derived_kdv(variants):
    _, base, _ = variants
    info = Selector().strategy().fetch_for_product(base)
    price = info.price
    assert price.exists
    assert price.is_tax_known
    assert price.incl_tax == D("349.90")
    assert price.excl_tax == D("318.09")
    assert price.tax == D("31.81")
    assert price.currency == "TRY"
    assert info.availability.is_available_to_buy


def test_strategy_uses_variant_override_and_product_kdv_override(variants):
    product, _, override = variants
    product.profile.kdv_rate = D(20)
    product.profile.save()
    price = Selector().strategy().fetch_for_product(override).price
    assert price.incl_tax == D("299.90")
    assert (price.excl_tax, price.tax) == pricing.split_kdv(D("299.90"), D(20))


def test_strategy_follows_shelf_price_changes_without_stock_sync(variants):
    product, base, _ = variants
    product.profile.shelf_price = D("400.00")
    product.profile.save()
    assert Selector().strategy().fetch_for_product(base).price.incl_tax == D("400.00")


def test_sold_out_variant_has_a_price_but_cannot_be_bought(variants):
    _, base, _ = variants
    record = base.stockrecords.get()
    record.num_in_stock = 0
    record.save()
    info = Selector().strategy().fetch_for_product(base)
    assert info.price.exists
    assert not info.availability.is_available_to_buy


def test_parent_price_is_the_cheapest_variant(variants):
    product, _, _ = variants
    add_ready_photo(product)
    catalog.set_status(product, ProductStatus.PUBLISHED)  # Oscar lists public children
    price = Selector().strategy().fetch_for_parent(product).price
    assert price.incl_tax == D("299.90")


def test_product_without_variants_has_no_price(variants):
    product, base, override = variants
    base.delete()
    override.delete()
    assert not Selector().strategy().fetch_for_parent(product).price.exists


def test_unit_split_and_line_snapshot_agree_on_incl_but_snapshot_is_authoritative():
    # 3 x 10.00 at 20%: per-unit split is 8.33 + 1.67, line-total split is 25.00 + 5.00.
    unit_excl, unit_kdv = pricing.split_kdv(D("10.00"), D(20))
    line = pricing.line_snapshot(unit_price=D("10.00"), quantity=3, kdv_rate=D(20))
    assert unit_excl * 3 + unit_kdv * 3 == line.total_incl
    assert (line.total_excl, line.total_kdv) == (D("25.00"), D("5.00"))
    assert unit_excl * 3 == D("24.99")  # why placement must use the snapshot
