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
from store_backend.services import approval_queue
from store_backend.services import checkout
from store_backend.services import home_layout
from store_backend.services import invoicing
from store_backend.services import order_context
from store_backend.services import order_ops
from store_backend.services import reports
from store_backend.services import returns
from store_backend.services import stock
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactReplyState
from store_backend.shop.models import Invoice
from store_backend.shop.models import InvoiceStatus
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from collections.abc import Callable
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
    window = order_context.PAYMENT_WINDOW
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


def open_returns() -> dict:
    """Return requests waiting for Staff to approve or reject."""
    return _capped(
        returns.awaiting_decision(),
        lambda r: {
            "return_id": r.pk,
            "number": r.order.number,
            "placed_at": r.requested_at,
            "customer": (
                f"{r.order.shipping_address.first_name} "
                f"{r.order.shipping_address.last_name}".strip()
                if r.order.shipping_address
                else ""
            ),
            "reason": r.reason,
            "item_count": sum(line.quantity for line in r.lines.all()),
        },
    )


#: The windows the Report summary shows, each as the Raporlar screen's own preset.
SUMMARY_PRESETS = (
    reports.Preset.TODAY,
    reports.Preset.LAST_7_DAYS,
    reports.Preset.LAST_30_DAYS,
)


def report_summary() -> dict:
    """Orders and revenue for a few windows. The figures are the Sales report's own
    totals (same preset, KDV included, same cache), so the two can never disagree."""
    periods = []
    for preset in SUMMARY_PRESETS:
        totals = reports.sales(reports.resolve(preset))["totals"]
        periods.append(
            {
                "preset": preset.value,
                "orders": totals["orders"],
                "revenue": totals["revenue"],
            },
        )
    return {"periods": periods}


EXCERPT = 160


def _excerpt(body: str) -> str:
    text = " ".join(body.split())
    return text if len(text) <= EXCERPT else text[: EXCERPT - 1].rstrip() + "…"


def contact_replies() -> dict:
    """Threads with a reply Staff wrote that waits for the Owner, oldest first.

    One row per thread (its oldest waiting reply), so the count matches the nav badge
    and the Contact list's "waiting" filter.
    """
    waiting = (
        ContactMessage.objects.filter(
            state=ContactReplyState.PENDING,
            thread__anonymised_at__isnull=True,
        )
        .select_related("thread__category", "author")
        .order_by("created_at", "pk")
    )
    first: dict[int, ContactMessage] = {}
    for reply in waiting:
        first.setdefault(reply.thread_id, reply)
    rows = [
        {
            "reply_id": r.pk,
            "thread_id": r.thread_id,
            "shopper": r.thread.name,
            "category": r.thread.category.name,
            "author": (
                {"id": r.author.pk, "name": r.author.name or r.author.email}
                if r.author
                else None
            ),
            "excerpt": _excerpt(r.body),
            "written_at": r.created_at,
        }
        for r in first.values()
    ]
    return {"count": len(rows), "rows": rows[:ROW_LIMIT]}


def approvals() -> dict:
    """Everything Staff submitted that waits for the Owner, oldest first."""
    rows = approval_queue.items()
    return {"count": len(rows), "rows": rows[:ROW_LIMIT]}


#: Widget key -> the list that fills it. A Widget that is off is never queried.
WIDGET_BUILDERS: dict[str, Callable[[datetime], dict]] = {
    "approvals": lambda _now: approvals(),
    "contact_replies": lambda _now: contact_replies(),
    "report_summary": lambda _now: report_summary(),
    "new_orders": lambda _now: new_orders(),
    "unpaid_transfers_near_expiry": unpaid_transfers_near_expiry,
    "open_returns": lambda _now: open_returns(),
    "low_stock": lambda _now: low_stock(),
}


def build(user, *, now: datetime | None = None) -> dict:
    """The Ana sayfa for `user`: their layout, what they may add, and the data of each
    Widget they have turned on.

    The sections that are not Widgets yet (invoices, Products without photos) keep being
    returned to anyone who may view Orders, as before, until they are decided on.
    """
    now = now or timezone.now()
    layout = home_layout.current(user)
    page: dict = {
        "layout": layout.keys,
        "collapsed": layout.collapsed,
        "available": [w.key for w in home_layout.available(user)],
        "is_default": layout.is_default,
    }
    for key in layout.keys:
        page[key] = WIDGET_BUILDERS[key](now)
    if user.has_perm(Perm.VIEW_ORDERS.permission_name):
        page.update(
            shipped_without_invoice=shipped_without_invoice(),
            invoice_attention=invoice_attention(),
            products_without_photos=products_without_photos(),
        )
    return page
