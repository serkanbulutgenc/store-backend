# ruff: noqa: TC001, TC003
"""Dashboard Home: the lists of things that need Staff attention."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Literal

from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.money import Money
from store_backend.api.permissions import requires
from store_backend.services import home
from store_backend.services import home_layout
from store_backend.shop.models import ReturnReason
from store_backend.staff.approvals_api import QueueItemOut

router = Router(tags=["home"])


class HomeOrderRow(Schema):
    number: str
    placed_at: datetime
    customer: str
    total: Money


class ExpiringRow(HomeOrderRow):
    expires_at: datetime


class InvoiceFlagRow(HomeOrderRow):
    invoice_number: str
    invoice_date: dt.date | None
    cancel_by: dt.date | None
    needed: Literal["cancel_invoice", "credit_note"]


class LowStockRow(Schema):
    variant_id: int
    product_id: int
    title: str
    sku: str
    colour: str
    size: str
    on_hand: int


class UnphotographedRow(Schema):
    product_id: int
    title: str


class HomeReturnRow(Schema):
    return_id: int
    number: str
    placed_at: datetime = Field(description="When the Shopper asked.")
    customer: str
    reason: ReturnReason
    item_count: int


class OpenReturns(Schema):
    count: int
    rows: list[HomeReturnRow]


class NewOrders(Schema):
    count: int
    rows: list[HomeOrderRow]


class ExpiringTransfers(Schema):
    count: int
    rows: list[ExpiringRow]


class InvoiceFlags(Schema):
    count: int
    rows: list[InvoiceFlagRow]


class LowStock(Schema):
    count: int
    rows: list[LowStockRow]


class ProductsWithoutPhotos(Schema):
    count: int
    rows: list[UnphotographedRow]


class ApprovalsWaiting(Schema):
    count: int
    rows: list[QueueItemOut]


class HomeOut(Schema):
    """`layout` is the Widgets turned on, in order; `available` is what the user
    may add. Each Widget's list is present only while it is turned on."""

    layout: list[str]
    available: list[str]
    is_default: bool
    approvals: ApprovalsWaiting | None = None
    open_returns: OpenReturns | None = None
    new_orders: NewOrders | None = None
    unpaid_transfers_near_expiry: ExpiringTransfers | None = None
    low_stock: LowStock | None = None
    shipped_without_invoice: NewOrders | None = None
    invoice_attention: InvoiceFlags | None = None
    products_without_photos: ProductsWithoutPhotos | None = None


class HomeLayoutIn(Schema):
    widgets: list[str]


# Open to every Dashboard user: `home_layout` filters each Widget by the permission it
# needs, so someone without it never receives its data.
@router.get(
    "/home",
    auth=requires(),
    operation_id="home_get",
    response=HomeOut,
    exclude_none=True,
)
def get_home(request):
    """Each list shows at most 20 rows and counts all of them."""
    return home.build(request.auth)


@router.put(
    "/home/layout",
    auth=requires(),
    operation_id="home_layout_save",
    response=HomeOut,
    exclude_none=True,
)
def save_layout(request, data: HomeLayoutIn):
    """Choose which Widgets are on, and their order. Unavailable ones are refused."""
    home_layout.save(request.auth, data.widgets)
    return home.build(request.auth)


@router.delete(
    "/home/layout",
    auth=requires(),
    operation_id="home_layout_reset",
    response=HomeOut,
    exclude_none=True,
)
def reset_layout(request):
    """Go back to the default for the user's role."""
    home_layout.reset(request.auth)
    return home.build(request.auth)
