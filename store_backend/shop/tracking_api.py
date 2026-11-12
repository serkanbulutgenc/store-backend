# ruff: noqa: TC001, TC003
"""Order tracking: by signed link or by Order number plus email or phone."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from django.conf import settings
from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.client import client_ip
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import TooManyRequestsError
from store_backend.api.money import Money
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.api.ratelimit import is_limited
from store_backend.services import order_ops
from store_backend.services import returns
from store_backend.services import tracking
from store_backend.shop.checkout_api import PlacedLineOut
from store_backend.shop.checkout_api import placed_summary
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import ReturnReason
from store_backend.shop.models import ReturnStatus

router = Router(tags=["tracking"], auth=GuestAuth())


class TrackingIn(Schema):
    number: str = Field(max_length=40)
    contact: str = Field(
        max_length=254,
        description="The email or the phone number on the Order.",
    )


class TrackingLineOut(PlacedLineOut):
    id: int
    returnable_quantity: int = Field(
        description="Units that could be requested for return right now.",
    )


class ReturnLineOut(Schema):
    line_id: int
    title: str
    quantity: int


class ReturnRequestOut(Schema):
    id: int
    status: ReturnStatus
    reason: ReturnReason
    note: str
    requested_at: datetime
    lines: list[ReturnLineOut]


class ReturnItemIn(Schema):
    line_id: int
    quantity: int


class ReturnIn(Schema):
    lines: list[ReturnItemIn]
    reason: ReturnReason
    note: str = Field("", max_length=500)


class TrackingOut(Schema):
    """What a Shopper may see: no phone, address or email."""

    number: str
    placed_at: datetime
    status: str
    payment_status: str
    payment_method: PaymentMethod
    lines: list[TrackingLineOut]
    items_total: Money
    shipping_fee: Money
    cod_fee: Money
    total: Money
    carrier: str | None
    tracking_number: str | None
    can_cancel: bool = Field(description="The Shopper may still cancel the Order.")
    return_deadline: datetime | None = Field(
        description="Last moment to request a return; null when none is possible.",
    )
    returns: list[ReturnRequestOut]


def order_out(order) -> dict:
    summary = placed_summary(order)
    carrier, tracking_number = tracking.shipment_of(order)
    returnable = returns.returnable_quantities(order)
    lines = [
        {**line, "returnable_quantity": returnable.get(line["id"], 0)}
        for line in summary["lines"]
    ]
    return {
        **{k: summary[k] for k in TrackingOut.model_fields if k in summary},
        "lines": lines,
        "placed_at": order.date_placed,
        "carrier": carrier,
        "tracking_number": tracking_number,
        "can_cancel": order_ops.RULES["cancel"].allows(order),
        "return_deadline": returns.deadline(order),
        "returns": returns.request_views(order),
    }


def cancel_order(number: str) -> dict:
    return order_out(order_ops.cancel_by_shopper(number))


def request_order_return(number: str, data: ReturnIn) -> dict:
    returns.request_return(
        number,
        [returns.Item(i.line_id, i.quantity) for i in data.lines],
        reason=data.reason,
        note=data.note,
    )
    return order_out(order_ops.get(number))


def _limit(request) -> None:
    count_attempt("tracking", client_ip(request), Rate(*settings.TRACKING_RATE_LIMIT))


@router.post("/tracking", operation_id="tracking_lookup", response=TrackingOut)
def lookup(request, data: TrackingIn):
    """Find an Order by number plus its email or phone. 404 for any mismatch;
    429 after repeated tries from one address or against one number."""
    _limit(request)
    # Only wrong guesses count against an Order number, so a stranger cannot lock the
    # owner out with right answers, and only well-formed numbers get a counter.
    number = tracking.normalise_number(data.number)
    rate = Rate(*settings.TRACKING_ORDER_RATE_LIMIT)
    guarded = tracking.is_order_number(number)
    if guarded and is_limited("tracking-order", number, rate):
        raise TooManyRequestsError
    try:
        order = tracking.order_for_lookup(data.number, data.contact)
    except NotFoundError:
        if guarded:
            count_attempt("tracking-order", number, rate)
        raise
    return order_out(order)


@router.get(
    "/tracking/link/{token}",
    operation_id="tracking_open_link",
    response=TrackingOut,
)
def open_link(request, token: str):
    """Open the Order a signed link was issued for (valid 30 days)."""
    _limit(request)
    return order_out(tracking.order_for_link(token))


@router.post(
    "/tracking/link/{token}/cancel",
    operation_id="tracking_cancel_order",
    response=TrackingOut,
)
def cancel_via_link(request, token: str):
    """Cancel the Order a signed link was issued for, while it is new or preparing.

    409 `illegal_transition` once it has shipped.
    """
    _limit(request)
    return cancel_order(tracking.order_for_link(token).number)


@router.post(
    "/tracking/link/{token}/returns",
    operation_id="tracking_request_return",
    response={HTTPStatus.CREATED: TrackingOut},
)
def return_via_link(request, token: str, data: ReturnIn):
    """Ask to return lines of a delivered Order (within 14 days, no hygiene lines).

    409 `return_not_available` or `return_window_closed`; 422 with `fields.lines`.
    """
    _limit(request)
    number = tracking.order_for_link(token).number
    return HTTPStatus.CREATED, request_order_return(number, data)
