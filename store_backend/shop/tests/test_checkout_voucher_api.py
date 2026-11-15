# ruff: noqa: PLR2004
"""Placing an Order over HTTP with a Voucher applied to the basket."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.test import Client
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import Voucher
from oscar.apps.voucher.models import VoucherApplication

from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import QUOTE
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import guest  # noqa: F401
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import quote_body
from store_backend.shop.tests.test_checkout import set_fees

pytestmark = pytest.mark.django_db

D = Decimal

BASKET = "/api/store/basket"
VOUCHER = "/api/store/basket/voucher"


def test_the_placed_order_carries_the_basket_voucher(guest, owner):  # noqa: F811
    set_fees(owner, shipping_fee="30.00", free_shipping_threshold=None)
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})  # the basket holds 2 x 100.00
    quoted = guest.post(QUOTE, quote_body("")).json()

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED, response.content
    placed = response.json()
    assert placed["items_total"] == "200.00"  # before the Voucher, as in the quote
    assert placed["discount"] == "20.00"
    assert placed["voucher_code"] == "YAZ10"
    assert placed["total"] == "210.00"  # 200.00 - 20.00 + 30.00
    assert {k: quoted[k] for k in ("items_total", "discount", "total")} == {
        k: placed[k] for k in ("items_total", "discount", "total")
    }
    assert Order.objects.get(number=placed["number"]).discounts.get().amount == D(
        "20.00",
    )


def test_a_voucher_that_stopped_working_refuses_the_order_and_keeps_the_basket(
    guest,  # noqa: F811
):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})
    Voucher.objects.update(
        start_datetime=timezone.now() - timedelta(days=10),
        end_datetime=timezone.now() - timedelta(days=1),
    )

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "voucher_invalid"
    assert response.json()["fields"] == {"voucher_code": ["expired"]}
    assert not Order.objects.exists()
    # The Shopper still has the basket and the code, and can remove it and carry on.
    assert guest.get(BASKET).json()["item_count"] == 2
    assert guest.get(BASKET).json()["voucher"]["code"] == "YAZ10"
    guest.delete(VOUCHER)
    assert guest.post(CHECKOUT, payload()).status_code == HTTPStatus.CREATED


def test_a_guest_cannot_use_a_once_per_customer_code_twice_under_one_email(shop):
    make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    first, second = Guest(), Guest(ip="203.0.113.8")
    for shopper in (first, second):
        fill(shopper, shop[1]["M"])
        # Applying works: a Guest's email is not known until checkout.
        assert shopper.post(VOUCHER, {"code": "ONCE10"}).status_code == HTTPStatus.OK
    assert first.post(CHECKOUT, payload(email="ayse@example.com")).status_code == 201

    response = second.post(CHECKOUT, payload(email="  Ayse@Example.com "))

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "voucher_invalid"
    assert response.json()["fields"] == {"voucher_code": ["already_used"]}
    assert Order.objects.count() == 1
    # A different email may still use it.
    other = second.post(CHECKOUT, payload(email="deniz@example.com"))
    assert other.status_code == HTTPStatus.CREATED, other.content
    assert other.json()["voucher_code"] == "ONCE10"


def test_a_customers_order_is_recorded_against_their_account(shop, customer, log_in):
    voucher = make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    signed_in = Guest(log_in(Client(), customer))
    fill(signed_in, shop[1]["M"])
    assert signed_in.post(VOUCHER, {"code": "ONCE10"}).status_code == HTTPStatus.OK

    response = signed_in.post(CHECKOUT, payload(email=customer.email))

    assert response.status_code == HTTPStatus.CREATED, response.content
    order = Order.objects.get(number=response.json()["number"])
    assert order.user == customer
    assert VoucherApplication.objects.get(voucher=voucher).user == customer
    # The account is now known, so a second try is refused as soon as it is applied.
    fill(signed_in, shop[1]["M"])
    again = signed_in.post(VOUCHER, {"code": "ONCE10"})
    assert again.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert again.json()["fields"] == {"voucher_code": ["already_used"]}


def test_the_placed_lines_add_up_to_the_items_before_the_voucher(guest, owner):  # noqa: F811
    set_fees(owner, shipping_fee="30.00", free_shipping_threshold=None)
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})

    placed = guest.post(CHECKOUT, payload()).json()

    line = placed["lines"][0]
    assert (line["unit_price"], line["line_total"]) == ("100.00", "200.00")
    lines_total = sum(D(item["line_total"]) for item in placed["lines"])
    assert lines_total == D(placed["items_total"])
    # Lines, the Voucher and shipping together give the total the Shopper pays.
    paid = lines_total - D(placed["discount"]) + D(placed["shipping_fee"])
    assert paid == D(placed["total"])
