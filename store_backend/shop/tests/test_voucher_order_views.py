"""What a Shopper sees of a Voucher on a placed Order: tracking page and account."""

from __future__ import annotations

from decimal import Decimal
from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.offer.models import Benefit

from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload

pytestmark = pytest.mark.django_db

LOOKUP = "/api/store/tracking"
LINK = "/api/store/tracking/link"
ORDERS = "/api/store/account/orders"
VOUCHER = "/api/store/basket/voucher"


def place(variant, voucher_code=None):
    buyer = Guest(ip="198.51.100.9")
    fill(buyer, variant)  # 2 x 100.00
    if voucher_code:
        applied = buyer.post(VOUCHER, {"code": voucher_code})
        assert applied.status_code == HTTPStatus.OK, applied.content
    response = buyer.post(CHECKOUT, payload())
    assert response.status_code == HTTPStatus.CREATED, response.content
    placed = response.json()
    assert placed["voucher_code"] == (voucher_code.upper() if voucher_code else None)
    return placed


def track(number):
    response = Guest(ip="203.0.113.60").post(
        LOOKUP,
        {"number": number, "contact": "ayse@example.com"},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def test_the_tracking_page_shows_the_voucher_and_the_figures_add_up(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place(shop[1]["M"], "yaz10")

    seen = track(placed["number"])

    assert (seen["voucher_code"], seen["discount"]) == ("YAZ10", "20.00")
    assert seen["items_total"] == "200.00"  # before the voucher, like the lines
    parts = (seen["items_total"], seen["shipping_fee"], seen["cod_fee"])
    charged = sum(Decimal(p) for p in parts) - Decimal(seen["discount"])
    assert charged == Decimal(seen["total"])


def test_an_order_without_a_voucher_shows_none_and_still_adds_up(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")  # exists, but not used
    placed = place(shop[1]["M"])

    seen = track(placed["number"])

    assert (seen["voucher_code"], seen["discount"]) == (None, "0.00")
    parts = (seen["items_total"], seen["shipping_fee"], seen["cod_fee"])
    assert sum(Decimal(p) for p in parts) == Decimal(seen["total"])


def test_the_tracking_link_and_the_account_show_the_same_voucher(
    shop,
    customer,
    log_in,
):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    buyer = Guest(log_in(Client(), customer))
    fill(buyer, shop[1]["M"])
    assert buyer.post(VOUCHER, {"code": "YAZ10"}).status_code == HTTPStatus.OK
    placed = buyer.post(CHECKOUT, payload(email=customer.email)).json()

    by_link = Guest().get(f"{LINK}/{placed['tracking_token']}").json()
    mine = buyer.get(f"{ORDERS}/{placed['number']}")

    assert mine.status_code == HTTPStatus.OK, mine.content
    for seen in (by_link, mine.json()):
        assert (seen["voucher_code"], seen["discount"]) == ("YAZ10", "20.00")
        assert seen["items_total"] == "200.00"
