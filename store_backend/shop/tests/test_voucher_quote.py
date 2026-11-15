"""Pricing a basket under a Voucher with `quote`: items discounted, fees untouched."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from oscar.apps.basket.models import Line
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import Condition
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.offer.models import Range
from oscar.apps.voucher.models import Voucher

from store_backend.services import quote as quoting
from store_backend.services import shop_settings
from store_backend.shop.models import PaymentMethod
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import fill

pytestmark = pytest.mark.django_db

D = Decimal


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


def make_voucher(
    benefit_type: str,
    value: str,
    *,
    minimum: str = "0",
    code: str = "YAZ10",
) -> Voucher:
    """A Voucher as the Dashboard will store it: Oscar's voucher, offer and benefit."""
    everything = Range.objects.create(name=f"all-{code}", includes_all_products=True)
    offer = ConditionalOffer.objects.create(
        name=f"offer-{code}",
        offer_type=ConditionalOffer.VOUCHER,
        condition=Condition.objects.create(
            range=everything,
            type=Condition.VALUE,
            value=D(minimum),
        ),
        benefit=Benefit.objects.create(
            range=everything,
            type=benefit_type,
            value=D(value),
        ),
    )
    now = timezone.now()
    voucher = Voucher.objects.create(
        name=f"voucher-{code}",
        code=code,
        start_datetime=now - timedelta(days=1),
        end_datetime=now + timedelta(days=30),
    )
    voucher.offers.add(offer)
    return voucher


def test_percentage_voucher_discounts_the_items_but_not_the_fees(lines):
    store = shop_settings.get()

    q = quoting.quote(
        lines,
        PaymentMethod.BANK_TRANSFER,
        store,
        voucher=make_voucher(Benefit.PERCENTAGE, "10"),
    )

    assert q.items_total == D("200.00")  # before the Voucher
    assert q.discount == D("20.00")
    assert sum(s.total_incl for s in q.snapshots.values()) == D("180.00")
    assert q.fees.shipping.total_incl == store.shipping_fee
    assert q.totals.total_incl == D("180.00") + store.shipping_fee


def test_voucher_is_not_applied_below_its_minimum_basket(lines):
    store = shop_settings.get()
    method = PaymentMethod.BANK_TRANSFER  # the items cost 200.00

    with pytest.raises(quoting.VoucherNotApplicableError):
        quoting.quote(
            lines,
            method,
            store,
            voucher=make_voucher(Benefit.PERCENTAGE, "10", minimum="250.00"),
        )

    # Exactly at the minimum it applies.
    q = quoting.quote(
        lines,
        method,
        store,
        voucher=make_voucher(
            Benefit.PERCENTAGE,
            "10",
            minimum="200.00",
            code="AT200",
        ),
    )
    assert q.discount == D("20.00")


def test_fixed_voucher_takes_its_amount_off_and_never_more_than_the_items(lines):
    store = shop_settings.get()
    method = PaymentMethod.BANK_TRANSFER  # the items cost 200.00

    q = quoting.quote(
        lines,
        method,
        store,
        voucher=make_voucher(Benefit.FIXED, "50.00"),
    )
    assert q.discount == D("50.00")
    assert sum(s.total_incl for s in q.snapshots.values()) == D("150.00")

    big = quoting.quote(
        lines,
        method,
        store,
        voucher=make_voucher(Benefit.FIXED, "500.00", code="BIG500"),
    )
    assert big.discount == D("200.00")  # capped at the items
    assert big.items_total == D("200.00")
    assert sum(s.total_incl for s in big.snapshots.values()) == D("0.00")
    assert big.totals.total_incl == store.shipping_fee


def test_free_shipping_is_decided_on_the_items_before_the_voucher(lines):
    store = shop_settings.get()
    store.free_shipping_threshold = D("200.00")  # the items cost exactly 200.00

    q = quoting.quote(
        lines,
        PaymentMethod.BANK_TRANSFER,
        store,
        voucher=make_voucher(Benefit.PERCENTAGE, "10"),
    )

    assert q.fees.shipping.total_incl == 0
    assert q.free_shipping
    assert q.totals.total_incl == D("180.00")
