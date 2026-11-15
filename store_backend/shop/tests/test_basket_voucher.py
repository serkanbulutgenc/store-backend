"""Applying a Voucher code to the basket over HTTP."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.voucher.models import Voucher

from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.conftest import redeem
from store_backend.shop.tests.test_checkout import fill

pytestmark = pytest.mark.django_db

BASKET = "/api/store/basket"
VOUCHER = "/api/store/basket/voucher"


def test_a_valid_code_is_applied_and_the_basket_shows_it(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)  # 2 x 100.00

    response = guest.post(VOUCHER, {"code": " yaz10 "})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["voucher"] == {
        "code": "YAZ10",
        "discount": "20.00",
        "minimum": None,
        "problem": None,
    }
    assert guest.get(BASKET).json()["voucher"] == {
        "code": "YAZ10",
        "discount": "20.00",
        "minimum": None,
        "problem": None,
    }


def test_an_unknown_code_is_refused_and_nothing_is_applied(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)

    response = guest.post(VOUCHER, {"code": "KIS20"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "voucher_invalid"
    assert response.json()["fields"] == {"voucher_code": ["invalid"]}
    assert guest.get(BASKET).json()["voucher"] is None


def test_a_guest_with_no_email_yet_is_not_taken_for_an_earlier_blank_email(shop):
    voucher = make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    redeem(voucher, email="")  # an earlier Order that has no email on it
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)

    response = guest.post(VOUCHER, {"code": "ONCE10"})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["voucher"]["code"] == "ONCE10"


def test_a_basket_below_the_minimum_is_refused_when_the_code_is_applied(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="BIG10", minimum="250.00")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)  # 200.00

    response = guest.post(VOUCHER, {"code": "BIG10"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["fields"] == {"voucher_code": ["minimum"]}
    assert guest.get(BASKET).json()["voucher"] is None


def test_a_second_code_replaces_the_first(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    make_voucher(Benefit.FIXED, "50.00", code="FIX50")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    guest.post(VOUCHER, {"code": "YAZ10"})

    response = guest.post(VOUCHER, {"code": "FIX50"})

    assert response.json()["voucher"] == {
        "code": "FIX50",
        "discount": "50.00",
        "minimum": None,
        "problem": None,
    }
    assert guest.get(BASKET).json()["voucher"]["code"] == "FIX50"


def test_a_refused_code_leaves_the_earlier_one_in_place(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    guest.post(VOUCHER, {"code": "YAZ10"})

    assert (
        guest.post(VOUCHER, {"code": "KIS20"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert guest.get(BASKET).json()["voucher"]["code"] == "YAZ10"


def test_the_voucher_can_be_removed(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    guest.post(VOUCHER, {"code": "YAZ10"})

    response = guest.delete(VOUCHER)

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["voucher"] is None
    assert guest.get(BASKET).json()["voucher"] is None
    # Removing again is harmless.
    assert guest.delete(VOUCHER).status_code == HTTPStatus.OK


def test_the_cart_flags_a_voucher_once_the_basket_falls_below_its_minimum(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="BIG10", minimum="250.00")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=3)  # 300.00
    assert guest.post(VOUCHER, {"code": "BIG10"}).status_code == HTTPStatus.OK
    line_id = guest.get(BASKET).json()["lines"][0]["id"]

    guest.patch(f"{BASKET}/lines/{line_id}", {"quantity": 2})  # 200.00 now

    assert guest.get(BASKET).json()["voucher"] == {
        "code": "BIG10",
        "discount": "0.00",
        "minimum": "250.00",
        "problem": "minimum",
    }


def test_a_code_cannot_be_applied_to_an_empty_basket():
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    response = Guest().post(VOUCHER, {"code": "YAZ10"})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "basket_empty"


def test_the_cart_flags_a_voucher_that_expired_since_it_was_applied(shop):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    guest.post(VOUCHER, {"code": "YAZ10"})
    Voucher.objects.update(
        start_datetime=timezone.now() - timedelta(days=10),
        end_datetime=timezone.now() - timedelta(days=1),
    )

    voucher = guest.get(BASKET).json()["voucher"]

    assert voucher["code"] == "YAZ10"
    assert voucher["discount"] == "0.00"
    assert voucher["problem"] == "expired"
