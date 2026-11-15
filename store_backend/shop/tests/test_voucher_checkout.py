"""Placing an Order with a Voucher code: what is charged, recorded and frozen."""

from __future__ import annotations

from decimal import Decimal

import pytest
from oscar.apps.basket.models import Basket
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import VoucherApplication
from oscar.core.loading import get_class

from store_backend.services import checkout
from store_backend.services import shop_settings
from store_backend.services import vouchers
from store_backend.services.contact import Address
from store_backend.shop.models import PaymentMethod
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import fill

pytestmark = pytest.mark.django_db

Selector = get_class("partner.strategy", "Selector")

D = Decimal
GUEST = checkout.Caller(user=None, ip_address="203.0.113.7", user_agent="TestBrowser")


def open_basket(variant) -> Basket:
    """A new basket of 2 x 100.00, priced the way a request would price it."""
    fill(Guest(), variant, quantity=2)
    basket = Basket.objects.get(status=Basket.OPEN)
    basket.strategy = Selector().strategy()  # the request middleware does this
    return basket


@pytest.fixture
def basket(shop):
    return open_basket(shop[1]["M"])


def place(basket: Basket, voucher_code: str | None = None, *, email="ayse@example.com"):
    form = checkout.CheckoutForm(
        full_name="Ayşe Yılmaz",
        email=email,
        phone="0532 123 45 67",
        shipping_address=Address(
            il="Mersin",
            ilce="Yenişehir",
            mahalle="Çiftlikköy Mah.",
            street="Gül Sok. No:5 D:3",
            postal_code="33110",
        ),
        billing_address=None,
        payment_method=PaymentMethod.BANK_TRANSFER,
        contract_acknowledged=True,
        voucher_code=voucher_code,
    )
    return checkout.place_order(basket, form, GUEST)


def test_a_voucher_lowers_the_items_the_order_charges(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    shipping = shop_settings.get().shipping_fee

    order = place(basket, "yaz10")

    assert order.lines.get().line_price_incl_tax == D("180.00")
    assert order.total_incl_tax == D("180.00") + shipping
    assert Order.objects.count() == 1


def test_placing_the_order_uses_up_a_redemption(shop, basket):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="ONE10")
    voucher.offers.update(max_global_applications=1)

    order = place(basket, "ONE10")

    assert VoucherApplication.objects.get(voucher=voucher).order == order
    voucher.refresh_from_db()
    assert voucher.num_orders == 1
    with pytest.raises(vouchers.VoucherError) as error:
        place(open_basket(shop[1]["M"]), "ONE10", email="deniz@example.com")
    assert error.value.reason == vouchers.Reason.EXHAUSTED
