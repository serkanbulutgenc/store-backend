"""Pricing a basket with `quote`: totals, fees and no database writes."""

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from oscar.apps.basket.models import Line

from store_backend.services import quote as quoting
from store_backend.services import shop_settings
from store_backend.shop.models import PaymentMethod
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import fill

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("computed_shipping")]

WRITES = ("INSERT", "UPDATE", "DELETE")


@pytest.fixture
def lines(shop):
    fill(Guest(), shop[1]["M"], quantity=2)  # 2 x 100.00
    return list(
        Line.objects.select_related(
            "product__variant_profile__colour",
            "product__parent__profile",
            "product__parent__product_class__profile",
        ),
    )


def price(lines, method=PaymentMethod.BANK_TRANSFER):
    return quoting.quote(lines, method, shop_settings.get())


def test_quote_prices_lines_and_fees_without_writing(lines):
    with CaptureQueriesContext(connection) as queries:
        q = price(lines)

    assert not [s for s in queries.captured_queries if s["sql"].startswith(WRITES)]
    store = shop_settings.get()
    assert q.items_total == Decimal("200.00")
    assert q.fees.shipping.total_incl == store.shipping_fee
    assert q.fees.cod is None
    assert q.totals.total_incl == Decimal("200.00") + store.shipping_fee


def test_cod_adds_the_cod_fee(lines):
    q = price(lines, PaymentMethod.COD)

    assert q.fees.cod is not None
    assert q.fees.cod.total_incl == shop_settings.get().cod_fee
    assert q.totals.total_incl == (
        q.items_total + q.fees.shipping.total_incl + q.fees.cod.total_incl
    )


def test_shipping_is_free_at_the_threshold(lines):
    store = shop_settings.get()
    store.free_shipping_threshold = Decimal("200.00")

    q = quoting.quote(lines, PaymentMethod.BANK_TRANSFER, store)

    assert q.fees.shipping.total_incl == 0


def test_quote_carries_the_settings_it_was_priced_with(lines):
    store = shop_settings.get()

    assert quoting.quote(lines, PaymentMethod.COD, store).store is store


def one_item(lines, desi):
    """A single-item basket (100.00) whose product weighs `desi`."""
    line = lines[0]
    line.quantity = 1
    profile = line.product.parent.profile
    profile.desi = desi
    profile.save()
    return [line]


def zone_4_price(lines, desi, province="Trabzon", **store_changes):
    store = shop_settings.get()
    for key, value in store_changes.items():
        setattr(store, key, value)
    return quoting.quote(
        one_item(lines, desi),
        PaymentMethod.BANK_TRANSFER,
        store,
        province=province,
    )


def test_zone_4_seven_desi_pays_the_cheapest_6_to_10_band(lines):
    q = zone_4_price(lines, 7)

    assert q.fees.shipping.total_incl == Decimal("125.00")  # PTT, zone 4, 6-10
    assert q.fees.shipping.total_excl + q.fees.shipping.total_kdv == Decimal("125.00")
    assert q.fees.shipping.kdv_rate == shop_settings.get().fee_kdv_rate


def test_order_desi_sums_desi_times_quantity(lines):
    line = one_item(lines, 3)[0]
    line.quantity = 4  # 12 desi: PTT zone 4, 125 + 2 * 10

    q = quoting.quote(
        [line],
        PaymentMethod.BANK_TRANSFER,
        shop_settings.get(),
        province="Trabzon",
    )

    assert q.fees.shipping.total_incl == Decimal("145.00")


def test_class_default_desi_applies_without_an_override(lines):
    line = lines[0]
    line.quantity = 1
    line.product.parent.product_class.profile.default_desi = 4

    q = quoting.quote(
        [line],
        PaymentMethod.BANK_TRANSFER,
        shop_settings.get(),
        province="Mersin",
    )

    assert q.fees.shipping.total_incl == Decimal("55.00")  # PTT, zone 1, 3-5


def test_free_shipping_threshold_still_wins(lines):
    q = zone_4_price(lines, 7, free_shipping_threshold=Decimal("100.00"))

    assert q.fees.shipping.total_incl == 0


def test_unknown_province_falls_back_to_the_flat_fee(lines):
    q = zone_4_price(lines, 7, province="Atlantis")

    assert q.fees.shipping.total_incl == shop_settings.get().shipping_fee


def test_no_province_falls_back_to_the_flat_fee(lines):
    q = zone_4_price(lines, 7, province=None)

    assert q.fees.shipping.total_incl == shop_settings.get().shipping_fee
