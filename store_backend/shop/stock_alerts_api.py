"""Stock alerts: ask to be emailed when a sold-out Variant is back."""

from __future__ import annotations

from http import HTTPStatus
from typing import Literal

from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.client import client_ip
from store_backend.services import stock_alerts

router = Router(tags=["stock-alerts"], auth=GuestAuth())


class SubscribeIn(Schema):
    variant_id: int
    email: str = Field(
        default="",
        max_length=254,
        description="Required for guests; ignored for a signed-in Shopper with a "
        "verified email.",
    )
    consent: bool = Field(
        description="The unticked consent box. Must be true.",
    )


class SubscribeOut(Schema):
    status: Literal["confirmed", "confirmation_sent"]


class TokenIn(Schema):
    token: str = Field(max_length=300)


@router.post(
    "/stock-alerts",
    operation_id="stock_alerts_subscribe",
    response=SubscribeOut,
)
def subscribe(request, data: SubscribeIn):
    """Ask to be emailed once when this sold-out Variant is back. A guest gets a
    confirm email (`confirmation_sent`); a signed-in Shopper with a verified email is
    `confirmed` at once. 409 `stock_alerts_unavailable` while the Store setting is
    off, 409 `in_stock` when the Variant is not sold out, 409 `stock_alert_limit` past
    5 active per email, 429 on too many tries from one address."""
    user = request.auth if request.auth.is_authenticated else None
    status = stock_alerts.subscribe(
        variant_id=data.variant_id,
        email=data.email,
        consent=data.consent,
        ip=client_ip(request),
        user=user,
    )
    return {"status": status}


@router.post(
    "/stock-alerts/confirm",
    operation_id="stock_alerts_confirm",
    response={HTTPStatus.OK: None},
)
def confirm(request, data: TokenIn):
    """The click in the confirm email, which is the consent. 404 for an unknown or
    expired link, 409 `in_stock` if the Variant came back meanwhile. Safe to repeat."""
    stock_alerts.confirm(data.token)
    return HTTPStatus.OK, None


@router.post(
    "/stock-alerts/reject",
    operation_id="stock_alerts_reject",
    response={HTTPStatus.OK: None},
)
def reject(request, data: TokenIn):
    """The reject link in every email. Deletes the waiting row and records the
    rejection; works while the setting is off, and repeats are fine. 404 for a
    tampered link."""
    stock_alerts.reject(data.token)
    return HTTPStatus.OK, None
