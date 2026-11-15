"""Cancelling an Order gives its Voucher Redemption back."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.voucher.models import VoucherApplication

from store_backend.services import order_context
from store_backend.services import order_ops
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_voucher_checkout import open_basket
from store_backend.shop.tests.test_voucher_checkout import place

pytestmark = pytest.mark.django_db


def test_cancelling_an_order_releases_its_redemption(shop, owner):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="ONE10")
    voucher.offers.update(max_global_applications=1)
    order = place(open_basket(shop[1]["M"]), "ONE10")

    order_ops.cancel(owner, order.number, reason="Müşteri vazgeçti")

    assert VoucherApplication.objects.count() == 0
    voucher.refresh_from_db()
    assert voucher.num_orders == 0
    # The code works again for someone else.
    second = place(open_basket(shop[1]["M"]), "ONE10", email="deniz@example.com")
    assert second.discounts.get().voucher_code == "ONE10"


def _used_once(shop):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="ONE10")
    voucher.offers.update(max_global_applications=1)
    return voucher, place(open_basket(shop[1]["M"]), "ONE10")


def test_the_shopper_cancelling_their_own_order_releases_it_too(shop):
    voucher, order = _used_once(shop)

    order_ops.cancel_by_shopper(order.number)

    assert VoucherApplication.objects.filter(voucher=voucher).count() == 0
    voucher.refresh_from_db()
    assert voucher.num_orders == 0


def test_the_automatic_cancel_of_an_unpaid_transfer_releases_it(shop):
    voucher, _ = _used_once(shop)
    after_the_window = (
        timezone.now() + order_context.PAYMENT_WINDOW + timedelta(hours=1)
    )

    assert order_ops.cancel_unpaid_transfers(now=after_the_window) == 1

    assert VoucherApplication.objects.filter(voucher=voucher).count() == 0
    voucher.refresh_from_db()
    assert voucher.num_orders == 0
