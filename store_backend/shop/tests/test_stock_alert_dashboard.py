from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone

from store_backend.shop.models import StockAlert
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

SETTINGS = "/api/admin/settings"
ADMIN = "/api/admin"


def waiting(variant, email, *, confirmed=True, expired=False):
    now = timezone.now()
    return StockAlert.objects.create(
        email=email,
        variant=variant,
        notice_version="1",
        confirm_token=f"t-{email}-{variant.pk}",
        confirmed_at=now if confirmed else None,
        expires_at=now + timedelta(days=-1 if expired else 30),
    )


def test_the_switch_is_off_by_default_and_the_owner_can_flip_it(owner, api_for):
    api = api_for(owner, enrol=True)
    assert api.get(SETTINGS).json()["stock_alerts_enabled"] is False

    response = api.patch(SETTINGS, {"stock_alerts_enabled": True})

    assert response.status_code == HTTPStatus.OK
    assert response.json()["stock_alerts_enabled"] is True
    entry = AuditEntry.objects.get(action="settings_change")
    assert entry.before["stock_alerts_enabled"] is False
    assert entry.after["stock_alerts_enabled"] is True


def test_staff_cannot_flip_the_switch(staff_user, api_for):
    api = api_for(staff_user)

    response = api.patch(SETTINGS, {"stock_alerts_enabled": True})

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_the_switch_must_be_a_boolean(owner, api_for):
    api = api_for(owner, enrol=True)

    response = api.patch(SETTINGS, {"stock_alerts_enabled": None})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_waiting_counts_confirmed_unexpired_rows_per_variant(shop, staff_user, api_for):
    product, variants = shop
    waiting(variants["L"], "a@example.com")
    waiting(variants["L"], "b@example.com")
    waiting(variants["L"], "pending@example.com", confirmed=False)
    waiting(variants["L"], "old@example.com", expired=True)
    waiting(variants["M"], "c@example.com")
    api = api_for(staff_user)

    body = api.get(f"{ADMIN}/products/{product.pk}").json()

    by_id = {v["id"]: v["stock"]["waiting"] for v in body["variants"]}
    assert by_id == {variants["L"].pk: 2, variants["M"].pk: 1}


def test_the_stock_grid_response_carries_the_count(shop, staff_user, api_for):
    product, variants = shop
    waiting(variants["L"], "a@example.com")
    api = api_for(staff_user)

    response = api.post(
        f"{ADMIN}/products/{product.pk}/stock-grid",
        {"reason": "Sayım", "cells": [{"variant_id": variants["M"].pk, "delta": 1}]},
    )

    counts = {v["id"]: v["stock"]["waiting"] for v in response.json()}
    assert counts == {variants["L"].pk: 1, variants["M"].pk: 0}


def test_a_single_variant_adjustment_carries_the_count(shop, staff_user, api_for):
    _, variants = shop
    waiting(variants["M"], "a@example.com")
    api = api_for(staff_user)

    response = api.post(
        f"{ADMIN}/variants/{variants['M'].pk}/stock-adjustments",
        {"delta": 1, "reason": "Sayım"},
    )

    assert response.json()["stock"]["waiting"] == 1


def test_no_email_address_reaches_the_dashboard(shop, owner, api_for):
    product, variants = shop
    waiting(variants["L"], "secret@example.com")
    api = api_for(owner, enrol=True)

    detail = api.get(f"{ADMIN}/products/{product.pk}").content
    settings = api.get(SETTINGS).content

    assert b"secret@example.com" not in detail + settings
