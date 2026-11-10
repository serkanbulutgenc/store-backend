# ruff: noqa: PLR2004
import time
from contextlib import contextmanager
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import signing
from oscar.apps.order.models import Order

from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload

pytestmark = pytest.mark.django_db


@contextmanager
def days_later(days, monkeypatch):
    now = time.time()
    with monkeypatch.context() as m:
        m.setattr(
            signing.time,
            "time",
            lambda: now + timedelta(days=days).total_seconds(),
        )
        yield


LOOKUP = "/api/store/tracking"
LINK = "/api/store/tracking/link"


@pytest.fixture
def placed(shop):
    _, v = shop
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, v["M"])
    response = buyer.post(CHECKOUT, payload())
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def lookup(number, contact, *, ip="203.0.113.50"):
    return Guest(ip=ip).post(LOOKUP, {"number": number, "contact": contact})


def test_placing_an_order_returns_a_tracking_token(placed):
    assert placed["tracking_token"]


def test_number_and_email_open_the_order(placed):
    response = lookup(placed["number"], "ayse@example.com")

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["number"] == placed["number"]
    assert body["status"] == "new"
    assert body["payment_status"] == "unpaid"
    assert body["total"] == placed["total"]
    assert len(body["lines"]) == 1
    assert body["carrier"] is None
    assert body["tracking_number"] is None
    assert "email" not in body
    assert "phone" not in str(body).lower()


def test_email_match_ignores_case_and_number_ignores_case_and_space(placed):
    response = lookup(f" {placed['number'].lower()} ", "AYSE@Example.com")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("phone", ["0532 123 45 67", "+90 532 123 45 67", "5321234567"])
def test_number_and_phone_open_the_order(placed, phone):
    assert lookup(placed["number"], phone).status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    "contact",
    ["someone@else.com", "0533 000 00 00", "garbage", ""],
)
def test_wrong_contact_looks_like_an_unknown_order(placed, contact):
    wrong = lookup(placed["number"], contact)
    unknown = lookup("AT-ZZZZZZ", "ayse@example.com")

    assert wrong.status_code == unknown.status_code == HTTPStatus.NOT_FOUND
    assert wrong.json() == unknown.json()


def test_the_link_opens_one_order(placed):
    response = Guest().get(f"{LINK}/{placed['tracking_token']}")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["number"] == placed["number"]


def test_the_link_expires_after_thirty_days(placed, monkeypatch):
    with days_later(29, monkeypatch):
        assert Guest().get(f"{LINK}/{placed['tracking_token']}").status_code == 200
    with days_later(31, monkeypatch):
        assert (
            Guest().get(f"{LINK}/{placed['tracking_token']}").status_code
            == HTTPStatus.NOT_FOUND
        )
        # Tracking by number plus email still works afterwards.
        assert lookup(placed["number"], "ayse@example.com").status_code == 200


def test_a_tampered_link_is_not_found(placed):
    token = placed["tracking_token"]
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert Guest().get(f"{LINK}/{bad}").status_code == HTTPStatus.NOT_FOUND


def test_a_link_for_another_purpose_is_not_accepted(placed):
    other = signing.dumps(placed["number"], salt="something-else")
    assert Guest().get(f"{LINK}/{other}").status_code == HTTPStatus.NOT_FOUND


def test_repeated_wrong_lookups_from_one_address_are_rate_limited(placed):
    codes = [
        lookup(f"AT-{i:06d}", "ayse@example.com", ip="203.0.113.99").status_code
        for i in range(30)
    ]
    assert codes[0] == HTTPStatus.NOT_FOUND
    assert HTTPStatus.TOO_MANY_REQUESTS in codes
    # Even the right answer waits once the address is limited.
    assert (
        lookup(placed["number"], "ayse@example.com", ip="203.0.113.99").status_code
        == HTTPStatus.TOO_MANY_REQUESTS
    )


def test_guessing_one_order_from_many_addresses_is_rate_limited(placed):
    codes = [
        lookup(
            placed["number"],
            f"guess{i}@example.com",
            ip=f"203.0.113.{i}",
        ).status_code
        for i in range(1, 20)
    ]
    assert HTTPStatus.TOO_MANY_REQUESTS in codes


def test_paid_and_refunded_orders_report_their_payment_status(placed):
    from store_backend.services import tracking  # noqa: PLC0415

    order = Order.objects.get(number=placed["number"])
    source = order.sources.get()
    source.amount_debited = source.amount_allocated
    source.save()
    assert tracking.payment_status_of(order) == "paid"
    source.amount_refunded = source.amount_debited
    source.save()
    assert tracking.payment_status_of(order) == "refunded"
