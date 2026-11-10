# ruff: noqa: TC001, TC003
"""Order tracking: by signed link or by Order number plus email or phone."""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.client import client_ip
from store_backend.api.money import Money
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.services import tracking
from store_backend.shop.checkout_api import PlacedLineOut
from store_backend.shop.checkout_api import placed_summary
from store_backend.shop.models import PaymentMethod

router = Router(tags=["tracking"], auth=GuestAuth())


class TrackingIn(Schema):
    number: str = Field(max_length=40)
    contact: str = Field(
        max_length=254,
        description="The email or the phone number on the Order.",
    )


class TrackingOut(Schema):
    """What a Shopper may see: no phone, address or email."""

    number: str
    placed_at: datetime
    status: str
    payment_status: str
    payment_method: PaymentMethod
    lines: list[PlacedLineOut]
    items_total: Money
    shipping_fee: Money
    cod_fee: Money
    total: Money
    carrier: str | None
    tracking_number: str | None


def _out(order) -> dict:
    summary = placed_summary(order)
    carrier, tracking_number = tracking.shipment_of(order)
    return {
        **{k: summary[k] for k in TrackingOut.model_fields if k in summary},
        "placed_at": order.date_placed,
        "carrier": carrier,
        "tracking_number": tracking_number,
    }


def _limit(request, number: str | None = None) -> None:
    count_attempt("tracking", client_ip(request), Rate(*settings.TRACKING_RATE_LIMIT))
    if number is not None:
        count_attempt(
            "tracking-order",
            tracking.normalise_number(number),
            Rate(*settings.TRACKING_ORDER_RATE_LIMIT),
        )


@router.post("/tracking", operation_id="tracking_lookup", response=TrackingOut)
def lookup(request, data: TrackingIn):
    """Find an Order by number plus its email or phone. 404 for any mismatch;
    429 after repeated tries from one address or against one number."""
    _limit(request, data.number)
    return _out(tracking.order_for_lookup(data.number, data.contact))


@router.get(
    "/tracking/link/{token}",
    operation_id="tracking_open_link",
    response=TrackingOut,
)
def open_link(request, token: str):
    """Open the Order a signed link was issued for (valid 30 days)."""
    _limit(request)
    return _out(tracking.order_for_link(token))
