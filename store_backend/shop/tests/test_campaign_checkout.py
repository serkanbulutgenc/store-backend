# ruff: noqa: PLR2004
"""Quote and checkout charge the Campaign price in force at that moment (GLOSSARY:
Campaign): Vouchers and Voucher minimums all see it."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Line
from oscar.apps.order.models import Order

from store_backend.catalog.models import Campaign
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_campaign_basket import campaign
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import QUOTE
from store_backend.shop.tests.test_checkout import guest  # noqa: F401
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import quote_body
from store_backend.shop.tests.test_checkout import set_fees

pytestmark = pytest.mark.django_db

BASKET = "/api/store/basket"
VOUCHER = "/api/store/basket/voucher"


def test_a_campaign_that_ended_before_checkout_is_not_charged(guest, shop, owner):  # noqa: F811
    product, _ = shop
    set_fees(owner, shipping_fee="30.00")
    made = campaign(20, product)  # in force when the Shopper added the items
    guest.post(QUOTE, quote_body(""))
    Campaign.objects.filter(pk=made.pk).update(
        ends_at=timezone.now() - timedelta(minutes=1),
    )
    quoted = guest.post(QUOTE, quote_body("")).json()

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED, response.content
    placed = response.json()
    assert placed["items_total"] == "200.00"  # back to the Shelf price
    assert placed["total"] == quoted["total"] == "230.00"
    assert Line.objects.get(order__number=placed["number"]).unit_price_incl_tax == 100


def test_a_campaign_that_started_after_the_basket_was_filled_is_charged(
    guest,  # noqa: F811
    shop,
    owner,
):
    product, _ = shop
    set_fees(owner, shipping_fee="30.00")
    campaign(20, product)

    body = guest.post(CHECKOUT, payload()).json()

    assert body["items_total"] == "160.00"
    assert body["total"] == "190.00"
    order = Order.objects.get(number=body["number"])
    assert order.total_incl_tax == 190


def test_a_voucher_applies_on_top_of_the_campaign_price(guest, shop, owner):  # noqa: F811
    product, _ = shop
    set_fees(owner, shipping_fee="30.00")
    campaign(20, product)  # 2 x 80.00
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})
    quoted = guest.post(QUOTE, quote_body("")).json()

    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["items_total"] == "160.00"
    assert placed["discount"] == "16.00"
    assert placed["total"] == "174.00"  # 160 - 16 + 30
    assert quoted["total"] == placed["total"]
    lines = Line.objects.filter(order__number=placed["number"])
    assert sum(line.line_price_incl_tax for line in lines) == 144
    assert sum(line.line_price_before_discounts_incl_tax for line in lines) == 160


def test_a_voucher_never_takes_the_items_below_zero(guest, shop, owner):  # noqa: F811
    product, _ = shop
    set_fees(owner, shipping_fee="30.00")
    campaign(20, product)
    make_voucher(Benefit.FIXED, "500", code="BUYUK")
    guest.post(VOUCHER, {"code": "BUYUK"})

    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["items_total"] == "160.00"
    assert placed["discount"] == "160.00"
    assert placed["total"] == "30.00"


def test_a_voucher_minimum_is_measured_on_campaign_prices(guest, shop):  # noqa: F811
    product, _ = shop
    campaign(20, product)  # 160.00 in the basket, 200.00 on the Shelf
    make_voucher(Benefit.PERCENTAGE, "10", minimum="200", code="MIN200")

    refused = guest.post(VOUCHER, {"code": "MIN200"})

    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert refused.json()["code"] == "voucher_invalid"


def test_a_voucher_whose_minimum_a_new_campaign_breaks_is_flagged(guest, shop):  # noqa: F811
    product, _ = shop
    make_voucher(Benefit.PERCENTAGE, "10", minimum="200", code="MIN200")
    assert guest.post(VOUCHER, {"code": "MIN200"}).status_code == HTTPStatus.OK

    campaign(20, product)
    body = guest.get(BASKET).json()

    assert body["subtotal"] == "160.00"
    assert body["voucher"]["problem"] == "minimum"
    assert body["voucher"]["discount"] == "0.00"
