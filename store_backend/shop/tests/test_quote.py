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

pytestmark = pytest.mark.django_db

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
