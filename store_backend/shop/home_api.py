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
from store_backend.shop.models import ReturnReason
from store_backend.staff.permissions import Perm

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


class HomeOut(Schema):
    new_orders: NewOrders
    unpaid_transfers_near_expiry: ExpiringTransfers
    low_stock: LowStock
    shipped_without_invoice: NewOrders
    invoice_attention: InvoiceFlags
    products_without_photos: ProductsWithoutPhotos
    open_returns: OpenReturns


@router.get(
    "/home",
    auth=requires(Perm.VIEW_ORDERS),
    operation_id="home_get",
    response=HomeOut,
)
def get_home(request):
    """Each list shows at most 20 rows and counts all of them."""
    return home.build()
