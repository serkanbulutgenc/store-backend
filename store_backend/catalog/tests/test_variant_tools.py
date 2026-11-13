# ruff: noqa: PLR2004
from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import VariantProfile
from store_backend.catalog.sizes import sizes_for
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

BASE = "/api/admin"


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


@pytest.fixture
def product():
    return catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Elbise"),
        shelf_price="100.00",
    )


@pytest.fixture
def colours():
    return [
        catalog.create_colour(name, f"#00000{i}")
        for i, name in enumerate(["Siyah", "Kırmızı", "Mavi"], start=1)
    ]


def sizes(product, n):
    return sizes_for(product.product_class.profile.size_scheme)[:n]


def generate(api, product, colours, size_list, **extra):
    body = {"colour_ids": [c.pk for c in colours], "sizes": size_list, **extra}
    return api.post(f"{BASE}/products/{product.pk}/variants/generate", body)


def test_three_colours_by_four_sizes_creates_twelve_then_skips_all(
    staff_api,
    product,
    colours,
):
    chosen = sizes(product, 4)
    first = generate(staff_api, product, colours, chosen, stock=3)
    assert first.status_code == HTTPStatus.CREATED, first.content
    assert len(first.json()["created"]) == 12
    assert first.json()["skipped"] == []
    assert VariantProfile.objects.filter(parent=product).count() == 12
    assert all(v["stock"]["on_hand"] == 3 for v in first.json()["created"])

    again = generate(staff_api, product, colours, chosen)
    assert again.status_code == HTTPStatus.CREATED
    assert again.json()["created"] == []
    assert len(again.json()["skipped"]) == 12
    assert VariantProfile.objects.filter(parent=product).count() == 12


def test_only_missing_combinations_are_created(staff_api, product, colours):
    catalog.create_variant(product, colour=colours[0], size=sizes(product, 1)[0])
    body = generate(staff_api, product, colours[:2], sizes(product, 2)).json()
    assert len(body["created"]) == 3
    assert len(body["skipped"]) == 1


def test_price_override_applies_and_skus_are_generated(staff_api, product, colours):
    body = generate(
        staff_api,
        product,
        colours[:1],
        sizes(product, 2),
        price_override="89.90",
    ).json()
    assert {v["price_override"] for v in body["created"]} == {"89.90"}
    assert all(v["sku"] for v in body["created"])
    assert len({v["sku"] for v in body["created"]}) == 2


def test_size_outside_scheme_is_refused_and_nothing_is_created(
    staff_api,
    product,
    colours,
):
    response = generate(staff_api, product, colours, [sizes(product, 1)[0], "ZZZ"])
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "sizes" in response.json()["fields"]
    assert not VariantProfile.objects.filter(parent=product).exists()


def test_inactive_colour_is_refused(staff_api, product, colours):
    catalog.update_colour(colours[1], is_active=False)
    response = generate(staff_api, product, colours, sizes(product, 1))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "colour_ids" in response.json()["fields"]
    assert not VariantProfile.objects.filter(parent=product).exists()


def test_unknown_colour_and_empty_selection_are_refused(staff_api, product, colours):
    size_list = sizes(product, 1)
    unknown = staff_api.post(
        f"{BASE}/products/{product.pk}/variants/generate",
        {"colour_ids": [999999], "sizes": size_list},
    )
    assert unknown.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert generate(staff_api, product, [], size_list).status_code == 422
    assert generate(staff_api, product, colours, []).status_code == 422


def test_generate_on_missing_product_is_404(staff_api, colours):
    response = staff_api.post(
        f"{BASE}/products/999999/variants/generate",
        {"colour_ids": [colours[0].pk], "sizes": ["M"]},
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.fixture
def grid(staff_api, product, colours):
    body = generate(staff_api, product, colours[:2], sizes(product, 2), stock=5).json()
    return body["created"]


def save_grid(api, product, cells, reason="Sayım"):
    return api.post(
        f"{BASE}/products/{product.pk}/stock-grid",
        {"reason": reason, "cells": [{"variant_id": i, "delta": d} for i, d in cells]},
    )


def test_three_edited_cells_write_three_audited_adjustments(staff_api, product, grid):
    cells = [
        (grid[0]["id"], 2),
        (grid[1]["id"], -1),
        (grid[2]["id"], 10),
        (grid[3]["id"], 0),
    ]
    response = save_grid(staff_api, product, cells)
    assert response.status_code == HTTPStatus.OK, response.content
    on_hand = {v["id"]: v["stock"]["on_hand"] for v in response.json()}
    assert on_hand == {
        grid[0]["id"]: 7,
        grid[1]["id"]: 4,
        grid[2]["id"]: 15,
        grid[3]["id"]: 5,
    }
    entries = AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT)
    assert entries.count() == 3
    assert all("Sayım" in e.summary for e in entries)


def test_unchanged_grid_writes_nothing(staff_api, product, grid):
    response = save_grid(staff_api, product, [(grid[0]["id"], 0)])
    assert response.status_code == HTTPStatus.OK
    assert not AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT).exists()


def test_stock_cannot_go_negative_and_nothing_is_applied(staff_api, product, grid):
    response = save_grid(staff_api, product, [(grid[0]["id"], 3), (grid[1]["id"], -6)])
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT).exists()
    again = save_grid(staff_api, product, [(grid[0]["id"], 0)]).json()
    assert {v["id"]: v["stock"]["on_hand"] for v in again}[grid[0]["id"]] == 5


def test_grid_needs_a_reason(staff_api, product, grid):
    response = save_grid(staff_api, product, [(grid[0]["id"], 1)], reason=" ")
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "reason" in response.json()["fields"]


def test_grid_refuses_variants_of_another_product(staff_api, product, colours, grid):
    other = catalog.create_product(
        title="Etek",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Etek"),
        shelf_price="50.00",
    )
    foreign = catalog.create_variant(
        other,
        colour=colours[0],
        size=sizes(other, 1)[0],
        stock=1,
    )
    response = save_grid(staff_api, product, [(foreign.pk, 1)])
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT).exists()


def test_both_require_a_signed_in_staff_user(client, product, colours):
    for path in ("variants/generate", "stock-grid"):
        response = client.post(
            f"{BASE}/products/{product.pk}/{path}",
            {},
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.UNAUTHORIZED, path
