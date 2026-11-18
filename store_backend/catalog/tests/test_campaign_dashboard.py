"""The Dashboard shows which Campaign is winning on a Product (GLOSSARY: Campaign)."""

from __future__ import annotations

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Campaign
from store_backend.services import catalog
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/admin/products"


@pytest.fixture
def boss(owner, api_for):  # noqa: F811
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


def make_product(title="Elbise"):
    return catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
    )


def campaign(name, percentage, *products, **fields):
    made = Campaign.objects.create(name=name, percentage=percentage, **fields)
    made.products.set(products)
    return made


def test_the_product_shows_its_winning_campaign(boss):
    product = make_product()
    campaign("Küçük", 10, product)
    big = campaign("Kara Cuma", 30, product)

    body = boss.get(f"{PRODUCTS}/{product.pk}").json()

    assert body["campaign"] == {
        "id": big.pk,
        "name": "Kara Cuma",
        "percentage": "30.00",
    }
    assert body["shelf_price"] == "100.00"  # the stored rule never rewrites the price


def test_a_product_without_a_running_campaign_shows_none(boss):
    product = make_product()
    campaign("Kapalı", 30, product, is_active=False)

    assert boss.get(f"{PRODUCTS}/{product.pk}").json()["campaign"] is None


def test_staff_see_the_winning_campaign_too(staff):
    product = make_product()
    made = campaign("Kara Cuma", 20, product)

    assert staff.get(f"{PRODUCTS}/{product.pk}").json()["campaign"]["id"] == made.pk


def test_the_product_list_shows_the_winner_per_row_without_a_query_each(
    boss,
    django_assert_max_num_queries,
):
    covered = [make_product(f"Ürün {i}") for i in range(6)]
    bare = make_product("Çıplak")
    made = campaign("Kara Cuma", 20, *covered)

    with django_assert_max_num_queries(25):
        rows = {p["id"]: p for p in boss.get(PRODUCTS).json()["items"]}

    assert rows[covered[0].pk]["campaign"]["id"] == made.pk
    assert rows[bare.pk]["campaign"] is None
