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
from store_backend.shop.campaign_schemas import AppliedCampaignOut

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
    unit_price: Money  # KDV-inclusive, whole kuruş; the Campaign price if one applies
    price_changed: bool = Field(
        description="The price differs from the one the Shopper last saw on this line.",
    )
    previous_unit_price: Money | None = Field(
        None,
        description="The price last seen, when `price_changed`.",
    )
    line_total: Money
    available: int
    is_hygiene: bool
    problem: str | None = Field(
        None,
        description="sold_out, insufficient_stock or unavailable; null when fine.",
    )


class VoucherOut(Schema):
    code: str
    discount: Money = Field(
        description="What it takes off the items; 0.00 while it has a problem.",
    )
    minimum: Money | None = Field(
        None,
        description="The items total the Voucher needs; null when it has no minimum.",
    )
    problem: str | None = Field(
        None,
        description="minimum when the basket fell below the minimum; null when fine.",
    )


class BasketOut(Schema):
    lines: list[BasketLineOut]
    item_count: int
    subtotal: Money
    has_problems: bool
    campaign: AppliedCampaignOut | None = Field(
        None,
        description="The Basket Campaign this basket gets, if any (shipping unknown: "
        "a free-shipping one is judged on the flat fee).",
    )
    voucher: VoucherOut | None = Field(
        None,
        description="The Voucher applied to this basket, if any.",
    )


class AddIn(Schema):
    variant_id: int
    quantity: int = Field(1, ge=1, le=basket_service.MAX_QUANTITY)


class VoucherIn(Schema):
    code: str = Field(max_length=128)


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


@router.post("/basket/voucher", operation_id="basket_apply_voucher", response=BasketOut)
def apply_voucher(request, data: VoucherIn):
    """Apply a Voucher code to the basket, replacing any earlier one."""
    user = request.user if request.user.is_authenticated else None
    basket_service.apply_voucher(request.basket, data.code, user)
    return basket_service.view(request.basket)


@router.delete(
    "/basket/voucher",
    operation_id="basket_remove_voucher",
    response=BasketOut,
)
def remove_voucher(request):
    """Take the Voucher off the basket. Harmless when there is none."""
    basket_service.remove_voucher(request.basket)
    return basket_service.view(request.basket)
