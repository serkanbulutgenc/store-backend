# ruff: noqa: PLR2004
"""Dashboard Customers list and detail (read-only)."""

from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.conftest import make_verified_user
from store_backend.services import customers
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload

pytestmark = pytest.mark.django_db

CUSTOMERS = "/api/admin/customers"


@pytest.fixture
def buyer(log_in, shop):
    """A signed-in customer with two orders."""
    user = make_verified_user(name="Ayşe Yılmaz", email="ayse@example.com")
    session = Guest(log_in(Client(), user))
    numbers = []
    for _ in range(2):
        fill(session, shop[1]["M"])
        response = session.post(CHECKOUT, payload(email=user.email))
        assert response.status_code == HTTPStatus.CREATED, response.content
        numbers.append(response.json()["number"])
    return user, numbers


def test_staff_without_the_permission_get_403(customer, api_for):
    assert api_for(customer).get(CUSTOMERS).status_code == HTTPStatus.FORBIDDEN


def test_anonymous_gets_401(client):
    assert client.get(CUSTOMERS).status_code == HTTPStatus.UNAUTHORIZED


def test_list_has_order_counts_and_skips_staff(buyer, staff_user, owner, api_for):
    user, _ = buyer
    make_verified_user(name="Zeynep", email="zeynep@example.com")

    response = api_for(staff_user).get(CUSTOMERS)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    rows = {row["email"]: row for row in body["items"]}
    assert set(rows) == {"ayse@example.com", "zeynep@example.com"}
    assert rows["ayse@example.com"]["order_count"] == 2
    assert rows["ayse@example.com"]["id"] == user.pk
    assert rows["zeynep@example.com"]["order_count"] == 0
    assert body["total"] == 2


def test_search_by_name_or_email(buyer, staff_user, api_for):
    make_verified_user(name="Zeynep", email="zeynep@example.com")
    api = api_for(staff_user)

    by_name = api.get(f"{CUSTOMERS}?q=yılmaz").json()["items"]
    by_email = api.get(f"{CUSTOMERS}?q=zeynep@").json()["items"]

    assert [r["email"] for r in by_name] == ["ayse@example.com"]
    assert [r["email"] for r in by_email] == ["zeynep@example.com"]


def test_detail_lists_the_customers_orders(buyer, staff_user, api_for):
    user, numbers = buyer

    response = api_for(staff_user).get(f"{CUSTOMERS}/{user.pk}")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["email"] == "ayse@example.com"
    assert body["name"] == "Ayşe Yılmaz"
    assert body["anonymised"] is False
    assert sorted(o["number"] for o in body["orders"]) == sorted(numbers)


def test_detail_of_a_staff_user_or_unknown_id_is_404(staff_user, owner, api_for):
    api = api_for(staff_user)

    assert api.get(f"{CUSTOMERS}/{owner.pk}").status_code == HTTPStatus.NOT_FOUND
    assert api.get(f"{CUSTOMERS}/999999").status_code == HTTPStatus.NOT_FOUND


def test_anonymised_customers_show_as_anonymised(buyer, staff_user, api_for):
    user, _ = buyer
    customers.anonymise(user)
    api = api_for(staff_user)

    row = api.get(CUSTOMERS).json()["items"][0]
    detail = api.get(f"{CUSTOMERS}/{user.pk}").json()

    for body in (row, detail):
        assert body["anonymised"] is True
        assert body["email"] == ""
        assert body["name"] == ""
    assert row["order_count"] == 2
    assert len(detail["orders"]) == 2
