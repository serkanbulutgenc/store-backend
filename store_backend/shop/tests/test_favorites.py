from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import customers
from store_backend.services import favorites
from store_backend.shop.models import Favorite
from store_backend.shop.tests.conftest import Guest

pytestmark = pytest.mark.django_db

LIST = "/api/store/account/favorites"


@pytest.fixture
def me(customer, log_in):
    return Guest(log_in(Client(), customer))


def url(product):
    return f"{LIST}/{product.pk}"


def another(product, *, title="Etek", stock=2, status=ProductStatus.PUBLISHED):
    from oscar.apps.catalogue.models import ProductCategory  # noqa: PLC0415

    category = ProductCategory.objects.get(product=product).category
    new = catalog.create_product(
        title=title,
        product_class=product.product_class,
        category=category,
        shelf_price="50.00",
    )
    catalog.create_variant(
        new,
        colour=catalog.create_colour(f"Renk {title}", "#ffffff"),
        size="M",
        stock=stock,
    )
    set_status(new, status)
    return new


def set_status(product, status):
    ProductProfile.objects.filter(product=product).update(status=status)


def test_guests_get_401(shop):
    product, _ = shop
    assert Guest().get(LIST).status_code == HTTPStatus.UNAUTHORIZED
    assert Guest().put(url(product), {}).status_code == HTTPStatus.UNAUTHORIZED


def test_dashboard_users_get_403(staff_user, log_in, shop):
    product, _ = shop
    staff = Guest(log_in(Client(), staff_user))
    assert staff.get(LIST).status_code == HTTPStatus.FORBIDDEN
    assert staff.put(url(product), {}).status_code == HTTPStatus.FORBIDDEN


def test_hearting_twice_is_idempotent(me, customer, shop):
    product, _ = shop

    first = me.put(url(product), {})
    second = me.put(url(product), {})

    assert first.status_code == second.status_code == HTTPStatus.NO_CONTENT
    assert Favorite.objects.filter(user=customer).count() == 1


def test_unhearting_removes_it_and_is_idempotent(me, customer, shop):
    product, _ = shop
    me.put(url(product), {})

    assert me.delete(url(product)).status_code == HTTPStatus.NO_CONTENT
    assert me.delete(url(product)).status_code == HTTPStatus.NO_CONTENT
    assert not Favorite.objects.filter(user=customer).exists()


def test_list_returns_cards_newest_first(me, shop):
    product, _ = shop
    other = another(product)
    me.put(url(product), {})
    me.put(url(other), {})

    body = me.get(LIST).json()

    assert [p["id"] for p in body] == [other.pk, product.pk]
    assert body[1]["slug"]
    assert body[1]["sold_out"] is False


def test_draft_and_archived_products_are_hidden(me, shop):
    product, _ = shop
    me.put(url(product), {})

    for status in (ProductStatus.DRAFT, ProductStatus.ARCHIVED):
        set_status(product, status)
        assert me.get(LIST).json() == []
    set_status(product, ProductStatus.PUBLISHED)
    assert len(me.get(LIST).json()) == 1


def test_sold_out_products_are_listed_flagged(me, shop):
    product, _ = shop
    sold_out = another(product, stock=0)
    me.put(url(sold_out), {})
    me.put(url(product), {})

    cards = {c["id"]: c for c in me.get(LIST).json()}

    assert cards[sold_out.pk]["sold_out"] is True
    assert cards[product.pk]["sold_out"] is False


def test_unpublished_product_cannot_be_hearted(me, shop):
    product, _ = shop
    set_status(product, ProductStatus.DRAFT)

    assert me.put(url(product), {}).status_code == HTTPStatus.NOT_FOUND
    assert me.put(f"{LIST}/999999", {}).status_code == HTTPStatus.NOT_FOUND


def test_the_201st_is_refused(me, customer, shop, monkeypatch):
    product, _ = shop
    monkeypatch.setattr(favorites, "LIMIT", 1)
    other = another(product)
    assert me.put(url(product), {}).status_code == HTTPStatus.NO_CONTENT

    response = me.put(url(other), {})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "favorites_limit"
    # Already-hearted Products still answer 204 at the limit.
    assert me.put(url(product), {}).status_code == HTTPStatus.NO_CONTENT


def test_account_anonymisation_removes_favorites(me, customer, shop):
    product, _ = shop
    me.put(url(product), {})

    customers.anonymise(customer)

    assert not Favorite.objects.filter(user=customer).exists()


def test_favorites_are_private_to_their_shopper(me, shop, log_in):
    from store_backend.conftest import make_verified_user  # noqa: PLC0415

    product, _ = shop
    me.put(url(product), {})
    other = Guest(log_in(Client(), make_verified_user()))

    assert other.get(LIST).json() == []
