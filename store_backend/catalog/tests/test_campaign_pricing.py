"""The Campaign price is computed from the rules on every read (GLOSSARY: Campaign,
ADR 0009). These tests go through the shared pricing function only."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Campaign
from store_backend.services import campaign_pricing
from store_backend.services import catalog

pytestmark = pytest.mark.django_db

D = Decimal


def make_product(title="Elbise", *, category=None, shelf="100.00"):
    return catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=category or catalog.create_category(f"{title} kategorisi"),
        shelf_price=shelf,
    )


def campaign(percentage, *, products=(), categories=(), collections=(), **fields):
    made = Campaign.objects.create(
        name=fields.pop("name", f"-{percentage}"),
        percentage=D(percentage),
        **fields,
    )
    made.products.set(products)
    made.categories.set(categories)
    made.collections.set(collections)
    return made


def test_the_largest_discount_wins_and_campaigns_never_stack():
    product = make_product()
    campaign(10, products=[product])
    big = campaign(30, products=[product], name="Kara Cuma")
    campaign(20, products=[product])

    winner = campaign_pricing.winners([product.pk])[product.pk]

    assert (winner.campaign_id, winner.name, winner.percentage) == (
        big.pk,
        "Kara Cuma",
        D(30),
    )
    assert campaign_pricing.discounted(D("100.00"), winner.percentage) == D("70.00")


def test_a_product_no_campaign_reaches_has_no_winner():
    product = make_product()
    other = make_product("Bluz")
    campaign(30, products=[other])

    assert campaign_pricing.winners([product.pk, other.pk]).keys() == {other.pk}


@pytest.mark.parametrize(
    ("starts", "ends", "applies"),
    [
        (None, None, True),  # open-ended
        (timedelta(seconds=-1), timedelta(hours=1), True),
        (timedelta(0), None, True),  # starts exactly now: applies
        (timedelta(seconds=1), None, False),  # not started yet
        (None, timedelta(0), False),  # ends exactly now: over
        (None, timedelta(seconds=1), True),
        (timedelta(hours=-2), timedelta(hours=-1), False),  # already over
    ],
)
def test_the_schedule_boundaries(starts, ends, applies):
    now = timezone.now()
    product = make_product()
    campaign(
        20,
        products=[product],
        starts_at=None if starts is None else now + starts,
        ends_at=None if ends is None else now + ends,
    )

    found = campaign_pricing.winners([product.pk], now=now)

    assert (product.pk in found) is applies


def test_the_manual_switch_overrides_the_schedule():
    product = make_product()
    campaign(
        20,
        products=[product],
        is_active=False,
        starts_at=timezone.now() - timedelta(days=1),
        ends_at=timezone.now() + timedelta(days=1),
    )

    assert campaign_pricing.winners([product.pk]) == {}


def test_a_category_reaches_its_subcategories_only_when_flagged():
    parent = catalog.create_category("Giyim")
    child = catalog.create_category("Elbise", parent)
    in_child = make_product("Elbise A", category=child)
    in_parent = make_product("Genel", category=parent)

    campaign(20, categories=[parent], include_subcategories=False)
    assert campaign_pricing.winners([in_child.pk, in_parent.pk]).keys() == {
        in_parent.pk,
    }

    Campaign.objects.update(include_subcategories=True)
    assert campaign_pricing.winners([in_child.pk, in_parent.pk]).keys() == {
        in_child.pk,
        in_parent.pk,
    }


@pytest.mark.parametrize(
    ("shelf", "percentage", "price"),
    [
        ("100.00", "20", "80.00"),
        ("199.90", "15", "169.92"),  # 169.915 rounds half up
        ("19.99", "33.33", "13.33"),  # 13.3267
        ("0.99", "50", "0.50"),  # 0.495 rounds half up
    ],
)
def test_the_price_is_rounded_half_up_to_kurus(shelf, percentage, price):
    assert campaign_pricing.discounted(D(shelf), D(percentage)) == D(price)


# --- whole store ---------------------------------------------------------------------


def test_a_whole_store_campaign_reaches_every_product():
    first = make_product("Elbise")
    second = make_product("Etek")
    made = campaign(15, whole_store=True)

    winners = campaign_pricing.winners([first.pk, second.pk])

    assert {pk: w.campaign_id for pk, w in winners.items()} == {
        first.pk: made.pk,
        second.pk: made.pk,
    }
    assert campaign_pricing.discounted(D("100.00"), winners[first.pk].percentage) == D(
        "85.00",
    )


def test_a_product_added_after_a_whole_store_campaign_is_covered_too():
    made = campaign(15, whole_store=True)
    later = make_product("Sonradan")

    assert campaign_pricing.winners([later.pk])[later.pk].campaign_id == made.pk


def test_a_whole_store_campaign_competes_by_the_largest_discount():
    product = make_product()
    other = make_product("Etek")
    campaign(10, whole_store=True)
    big = campaign(30, products=[product], name="Sadece elbise")

    winners = campaign_pricing.winners([product.pk, other.pk])

    assert winners[product.pk].campaign_id == big.pk  # the larger one wins
    assert winners[other.pk].percentage == D(
        10,
    )  # the other Product keeps the store-wide one


def test_a_whole_store_campaign_ignores_its_listed_targets():
    listed = make_product("Listed")
    other = make_product("Other")
    campaign(10, whole_store=True, products=[listed])

    assert set(campaign_pricing.winners()) == {listed.pk, other.pk}


def test_an_ended_or_switched_off_whole_store_campaign_prices_nothing():
    product = make_product()
    campaign(10, whole_store=True, is_active=False)

    assert campaign_pricing.winners([product.pk]) == {}
