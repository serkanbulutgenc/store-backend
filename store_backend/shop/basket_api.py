# ruff: noqa: TC001
"""Basket endpoints. Guests use Oscar's signed cookie plus a CSRF check."""

from __future__ import annotations

from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.money import Money
from store_backend.catalog.store_api import CardImageOut
from store_backend.services import basket as basket_service

router = Router(tags=["basket"], auth=GuestAuth())

Quantity = Field(ge=1, le=basket_service.MAX_QUANTITY)


class ColourOut(Schema):
    name: str
    hex_code: str


class BasketLineOut(Schema):
    id: int
    variant_id: int
    sku: str
    title: str
    slug: str
    colour: ColourOut
    size: str
    image: CardImageOut | None
    quantity: int
    unit_price: Money  # KDV-inclusive, whole kuruş
    line_total: Money
    available: int
    is_hygiene: bool
    problem: str | None = Field(
        None,
        description="sold_out, insufficient_stock or unavailable; null when fine.",
    )


class BasketOut(Schema):
    lines: list[BasketLineOut]
    item_count: int
    subtotal: Money
    has_problems: bool


class AddIn(Schema):
    variant_id: int
    quantity: int = Field(1, ge=1, le=basket_service.MAX_QUANTITY)


class QuantityIn(Schema):
    quantity: int = Quantity


@router.get("/basket", operation_id="basket_get", response=BasketOut)
def get_basket(request):
    return basket_service.view(request.basket)


@router.post("/basket/lines", operation_id="basket_add_line", response=BasketOut)
def add_line(request, data: AddIn):
    basket_service.add(request.basket, data.variant_id, data.quantity)
    return basket_service.view(request.basket)


@router.patch(
    "/basket/lines/{line_id}",
    operation_id="basket_set_quantity",
    response=BasketOut,
)
def set_quantity(request, line_id: int, data: QuantityIn):
    basket_service.set_quantity(request.basket, line_id, data.quantity)
    return basket_service.view(request.basket)


@router.delete(
    "/basket/lines/{line_id}",
    operation_id="basket_remove_line",
    response=BasketOut,
)
def remove_line(request, line_id: int):
    basket_service.remove(request.basket, line_id)
    return basket_service.view(request.basket)
