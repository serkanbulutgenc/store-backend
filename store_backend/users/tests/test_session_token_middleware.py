"""Build check 1: the X-Session-Token bridge keeps Oscar's basket merge working."""

from decimal import Decimal

import pytest
from django.test import Client
from oscar.apps.basket.middleware import BasketMiddleware
from oscar.apps.basket.models import Basket
from oscar.apps.partner.strategy import Default
from oscar.test.factories import create_product

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("store_backend.api.tests.urls"),
]

COOKIE = "oscar_open_basket"


def new_product(title):
    return create_product(title=title, price=Decimal("10.00"), num_in_stock=10)


def basket_with(products, owner=None):
    basket = Basket.objects.create(owner=owner)
    basket.strategy = Default()
    for product in products:
        basket.add_product(product, 1)
    return basket


def guest_client(basket):
    client = Client()
    client.cookies[COOKIE] = BasketMiddleware(None).get_basket_hash(basket.id)
    return client


def test_token_sets_request_user(customer, log_in):
    token = log_in(Client(), customer)

    body = Client().get("/t/basket", headers={"X-Session-Token": token}).json()

    assert body["user"] == customer.pk


def test_no_token_means_anonymous():
    assert Client().get("/t/basket").json()["user"] is None


def test_invalid_token_means_anonymous():
    response = Client().get("/t/basket", headers={"X-Session-Token": "bogus"})

    assert response.json()["user"] is None


def test_guest_basket_merges_into_the_customer_basket_on_login(customer, log_in):
    shirt, scarf, socks = (
        new_product("shirt"),
        new_product("scarf"),
        new_product("socks"),
    )
    basket_with([shirt, scarf], owner=customer)  # customer's existing basket
    guest = basket_with([socks])
    client = guest_client(guest)

    token = log_in(client, customer)  # login carries the guest basket cookie
    body = client.get("/t/basket", headers={"X-Session-Token": token}).json()

    assert body["owner"] == customer.pk
    assert body["products"] == sorted([shirt.pk, scarf.pk, socks.pk])
    guest.refresh_from_db()
    assert guest.status == Basket.MERGED


def test_customer_baskets_load_unchanged_with_a_token(customer, log_in):
    shirt, scarf = new_product("shirt"), new_product("scarf")
    saved = basket_with([shirt, scarf], owner=customer)
    token = log_in(Client(), customer)

    body = Client().get("/t/basket", headers={"X-Session-Token": token}).json()

    assert body["basket_id"] == saved.id
    assert body["products"] == sorted([shirt.pk, scarf.pk])


def test_guest_basket_is_untouched_without_a_token():
    shirt = new_product("shirt")
    guest = basket_with([shirt])

    body = guest_client(guest).get("/t/basket").json()

    assert body["basket_id"] == guest.id
    assert body["owner"] is None
    assert body["products"] == [shirt.pk]
