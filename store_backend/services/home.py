"""The Dashboard Home lists: what needs Staff attention right now.

Every list is capped at `ROW_LIMIT` rows and carries the full `count`, so the
Dashboard can say "and 14 more" without loading them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Exists
from django.db.models import OuterRef
from django.utils import timezone
from oscar.apps.catalogue.models import Product
from oscar.apps.order.models import Order

from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductStatus
from store_backend.services import checkout
from store_backend.services import invoicing
from store_backend.services import order_ops
from store_backend.services import outbox
from store_backend.services import stock
from store_backend.shop.models import Invoice
from store_backend.shop.models import InvoiceStatus

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

ROW_LIMIT = 20
NEAR_EXPIRY = timedelta(hours=12)  # the last stretch of the 48 h payment window
SHIPPED = (
    order_ops.STATUS_SHIPPED,
    order_ops.STATUS_DELIVERED,
    order_ops.STATUS_COMPLETED,
)
UNDONE = (order_ops.STATUS_CANCELLED, order_ops.STATUS_RETURNED)


def _orders() -> QuerySet[Order]:
    return Order.objects.select_related("user", "shipping_address", "invoice")


def _order_row(order: Order, **extra) -> dict:
    address = order.shipping_address
    return {
        "number": order.number,
        "placed_at": order.date_placed,
        "customer": (
            f"{address.first_name} {address.last_name}".strip() if address else ""
        ),
        "total": order.total_incl_tax,
        **extra,
    }


def _capped(rows: QuerySet, make) -> dict:
    return {"count": rows.count(), "rows": [make(row) for row in rows[:ROW_LIMIT]]}


def new_orders() -> dict:
    rows = _orders().filter(status=checkout.STATUS_NEW).order_by("-date_placed", "-pk")
    return _capped(rows, _order_row)


def unpaid_transfers_near_expiry(now: datetime) -> dict:
    window = outbox.PAYMENT_WINDOW
    rows = order_ops.unpaid_transfers(now - (window - NEAR_EXPIRY))
    rows = rows.select_related("shipping_address").order_by("date_placed", "pk")
    return _capped(
        rows,
        lambda o: _order_row(o, expires_at=o.date_placed + window),
    )


def shipped_without_invoice() -> dict:
    invoiced = Invoice.objects.filter(order=OuterRef("pk")).exclude(
        status=InvoiceStatus.NOT_ISSUED,
    )
    rows = (
        _orders()
        .filter(status__in=SHIPPED)
        .exclude(Exists(invoiced))
        .order_by("date_placed", "pk")
    )
    return _capped(rows, _order_row)


def invoice_attention() -> dict:
    """Issued invoices of cancelled or returned Orders with no credit note yet."""
    rows = (
        _orders()
        .filter(
            status__in=UNDONE,
            invoice__status=InvoiceStatus.ISSUED,
            invoice__credit_note_number="",
        )
        .order_by("date_placed", "pk")
    )
    today = timezone.localdate()
    return _capped(
        rows,
        lambda o: _order_row(
            o,
            invoice_number=o.invoice.number,
            invoice_date=o.invoice.date,
            cancel_by=invoicing.cancel_by(o.invoice),
            needed=invoicing.needed(o.invoice, today=today),
        ),
    )


def low_stock() -> dict:
    return _capped(
        stock.low_stock_variants(),
        lambda v: {
            "variant_id": v.pk,
            "product_id": v.parent_id,
            "title": v.parent.title,
            "sku": v.variant_profile.sku,
            "colour": v.variant_profile.colour.name,
            "size": v.variant_profile.size,
            "on_hand": v.free,
        },
    )


def products_without_photos() -> dict:
    has_photo = ProductPhoto.objects.filter(product=OuterRef("pk")).exclude(
        status=ProductPhoto.Status.FAILED,
    )
    rows = (
        Product.objects.filter(
            structure__in=(Product.PARENT, Product.STANDALONE),
            profile__status=ProductStatus.PUBLISHED,
        )
        .exclude(Exists(has_photo))
        .select_related("profile")
        .order_by("title", "pk")
    )
    return _capped(rows, lambda p: {"product_id": p.pk, "title": p.title})


def build(*, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    return {
        "new_orders": new_orders(),
        "unpaid_transfers_near_expiry": unpaid_transfers_near_expiry(now),
        "low_stock": low_stock(),
        "shipped_without_invoice": shipped_without_invoice(),
        "invoice_attention": invoice_attention(),
        "products_without_photos": products_without_photos(),
    }
