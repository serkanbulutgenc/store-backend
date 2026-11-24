"""The checkout quote, priced with the Voucher applied to the basket."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.test import Client
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.voucher.models import Voucher

from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.conftest import redeem
from store_backend.shop.tests.test_checkout import QUOTE
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import guest  # noqa: F401
from store_backend.shop.tests.test_checkout import quote_body
from store_backend.shop.tests.test_checkout import set_fees

pytestmark = pytest.mark.django_db

BASKET = "/api/store/basket"
VOUCHER = "/api/store/basket/voucher"


def test_the_quote_discounts_the_items_and_keeps_the_fees(guest, owner):  # noqa: F811
    set_fees(owner, shipping_fee="30.00")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})  # the basket holds 2 x 100.00

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["items_total"] == "200.00"
    assert quoted["discount"] == "20.00"
    assert quoted["voucher_code"] == "YAZ10"
    assert quoted["shipping_fee"] == "30.00"
    assert quoted["total"] == "210.00"  # 200.00 - 20.00 + 30.00


def test_a_basket_that_fell_below_the_minimum_is_quoted_without_the_discount(
    shop,
    owner,
):
    set_fees(owner, shipping_fee="30.00")
    make_voucher(Benefit.PERCENTAGE, "10", code="BIG10", minimum="250.00")
    shopper = Guest()
    fill(shopper, shop[1]["M"], quantity=3)  # 300.00
    shopper.post(VOUCHER, {"code": "BIG10"})
    line_id = shopper.get(BASKET).json()["lines"][0]["id"]
    shopper.patch(f"{BASKET}/lines/{line_id}", {"quantity": 2})  # 200.00 now

    response = shopper.post(QUOTE, quote_body(""))

    assert response.status_code == HTTPStatus.OK, response.content
    quoted = response.json()
    assert quoted["discount"] == "0.00"
    assert quoted["voucher_code"] == "BIG10"
    assert quoted["voucher_problem"] == "minimum"
    assert quoted["total"] == "230.00"  # 200.00 + 30.00, no discount


def test_an_expired_voucher_is_quoted_without_it(guest, owner):  # noqa: F811
    set_fees(owner, shipping_fee="30.00")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})
    Voucher.objects.update(
        start_datetime=timezone.now() - timedelta(days=10),
        end_datetime=timezone.now() - timedelta(days=1),
    )

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["discount"] == "0.00"
    assert quoted["voucher_code"] == "YAZ10"
    assert quoted["voucher_problem"] == "expired"
    assert quoted["total"] == "230.00"  # 200.00 + 30.00, no discount


def test_a_customer_who_used_a_once_per_customer_code_meanwhile_is_told(
    shop,
    owner,
    customer,
    log_in,
):
    set_fees(owner, shipping_fee="30.00")
    voucher = make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    signed_in = Guest(log_in(Client(), customer))
    fill(signed_in, shop[1]["M"], quantity=2)
    assert signed_in.post(VOUCHER, {"code": "ONCE10"}).status_code == HTTPStatus.OK
    redeem(voucher, user=customer, email=customer.email)  # used on another device

    quoted = signed_in.post(QUOTE, quote_body("")).json()

    assert quoted["discount"] == "0.00"
    assert quoted["voucher_problem"] == "already_used"
    assert quoted["total"] == "230.00"
