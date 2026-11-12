"""The Owner's CSV for the accountant: one row per invoice line, for a date range.

Amounts come from the frozen KDV snapshots (the same figures as the invoice sheet).
Email, phone and notes are never written. The range is by the day the Order was
placed, in the shop's time zone, both ends inclusive.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import invoicing
from store_backend.services import order_ops
from store_backend.services import payment
from store_backend.shop.models import Invoice

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from oscar.apps.order.models import Order

    from store_backend.users.models import User

MAX_DAYS = 366
COLUMNS = (
    "order_number",
    "order_status",
    "order_date",
    "ship_date",
    "buyer_name",
    "buyer_tax_number",
    "buyer_company_title",
    "billing_address",
    "line_kind",
    "description",
    "quantity",
    "kdv_rate",
    "amount_excl",
    "kdv_amount",
    "amount_incl",
    "order_total_incl",
    "payment_method",
    "paid_date",
    "carrier",
    "invoice_number",
    "invoice_status",
)
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe(value: object) -> str:
    """Text for a CSV cell, defanged so a spreadsheet never runs it as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def _day(value: datetime | None) -> str:
    return str(timezone.localtime(value).date()) if value else ""


def orders_between(start: date, end: date) -> Iterable[Order]:
    zone = timezone.get_current_timezone()
    first = datetime.combine(start, time.min, tzinfo=zone)
    after = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone)
    return (
        order_ops.detail_queryset()
        .filter(date_placed__gte=first, date_placed__lt=after)
        .order_by("date_placed", "pk")
    )


def _rows(order: Order, invoice: Invoice | None):
    sheet = invoicing.sheet(order)
    record = order_ops.peek_record(order)
    buyer = sheet["buyer"]
    address = ", ".join(
        part
        for part in (
            buyer["street"],
            buyer["mahalle"],
            f"{buyer['ilce']}/{buyer['il']}".strip("/"),
            buyer["postal_code"],
        )
        if part
    )
    common = [
        order.number,
        order.status,
        _day(order.date_placed),
        _day(record.shipped_at),
        buyer["name"],
        buyer["tax_number"],
        buyer["company_title"],
        address,
    ]
    tail = [
        sheet["total_incl"],
        payment.payment_method_of(order).value,
        _day(record.paid_at),
        record.carrier,
        invoice.number if invoice else "",
        invoice.status if invoice else "not_issued",
    ]
    for line in sheet["lines"]:
        yield [
            *common,
            line["kind"],
            line["description"],
            line["quantity"],
            line["kdv_rate"],
            line["total_excl"],
            line["total_kdv"],
            line["total_incl"],
            *tail,
        ]


@transaction.atomic
def export(actor: User, start: date, end: date) -> str:
    """The CSV text for Orders placed from `start` to `end`; writes the audit entry."""
    if end < start:
        msg = "Bitiş tarihi başlangıçtan önce olamaz."
        raise UnprocessableError(msg, fields={"to": [msg]})
    if (end - start).days >= MAX_DAYS:
        msg = f"En fazla {MAX_DAYS} günlük aralık seçilebilir."
        raise UnprocessableError(msg, fields={"to": [msg]})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    count = 0
    for order in orders_between(start, end):
        count += 1
        invoice = Invoice.objects.filter(order=order).first()
        for row in _rows(order, invoice):
            writer.writerow([safe(cell) for cell in row])
    audit.record(
        actor,
        audit.Action.EXPORT_RUN,
        target=("accountant_csv", f"{start}..{end}"),
        summary=f"Muhasebe CSV: {start} – {end} ({count} sipariş)",
        after={"from": str(start), "to": str(end), "orders": count},
    )
    return out.getvalue()
