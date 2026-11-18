"""The basket reprices live on the Campaign price (GLOSSARY: Campaign): a Campaign that
starts or ends changes the line, and the basket says so."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from store_backend.catalog.models import Campaign
from store_backend.catalog.models import VariantProfile
from store_backend.shop.tests.conftest import Guest

pytestmark = pytest.mark.django_db

BASKET = "/api/store/basket"
LINES = "/api/store/basket/lines"


def campaign(percentage, product, **fields):
    made = Campaign.objects.create(
        name=fields.pop("name", f"-{percentage}"),
        percentage=percentage,
        **fields,
    )
    made.products.set([product])
    return made


def test_a_line_is_priced_on_the_campaign_and_not_flagged(shop):
    product, v = shop
    campaign(20, product)
    guest = Guest()

    body = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2}).json()

    line = body["lines"][0]
    assert (line["unit_price"], line["line_total"]) == ("80.00", "160.00")
    assert body["subtotal"] == "160.00"
    assert line["price_changed"] is False
    assert line["previous_unit_price"] is None


def test_a_variant_override_is_discounted_too(shop):
    product, v = shop
    VariantProfile.objects.filter(product=v["M"]).update(price_override="80.00")
    campaign(25, product)

    line = Guest().post(LINES, {"variant_id": v["M"].pk}).json()["lines"][0]

    assert line["unit_price"] == "60.00"


def test_a_campaign_that_starts_after_the_add_flags_the_line(shop):
    product, v = shop
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk})

    campaign(20, product)
    line = guest.get(BASKET).json()["lines"][0]

    assert line["unit_price"] == "80.00"
    assert line["price_changed"] is True
    assert line["previous_unit_price"] == "100.00"


def test_a_campaign_that_ends_after_the_add_flags_the_line(shop):
    product, v = shop
    made = campaign(20, product, ends_at=timezone.now() + timedelta(hours=1))
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk})

    Campaign.objects.filter(pk=made.pk).update(
        ends_at=timezone.now() - timedelta(minutes=1),
    )
    body = guest.get(BASKET).json()

    assert body["lines"][0]["unit_price"] == "100.00"
    assert body["lines"][0]["price_changed"] is True
    assert body["lines"][0]["previous_unit_price"] == "80.00"
    assert body["subtotal"] == "100.00"


def test_changing_the_quantity_takes_the_new_price_as_seen(shop):
    product, v = shop
    guest = Guest()
    line_id = guest.post(LINES, {"variant_id": v["M"].pk}).json()["lines"][0]["id"]
    campaign(20, product)
    assert guest.get(BASKET).json()["lines"][0]["price_changed"] is True

    body = guest.patch(f"{LINES}/{line_id}", {"quantity": 2}).json()

    assert body["lines"][0]["price_changed"] is False
    assert body["lines"][0]["unit_price"] == "80.00"
