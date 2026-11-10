# ruff: noqa: EM101, TRY003
"""The invoice record, the invoice sheet and the "needs an invoice" flags.

Invoices are issued by hand in the GIB e-Arsiv portal; the store only records the
number, date and status Staff type back in. Within 8 days of the invoice date the
portal lets Staff cancel an invoice; after that a credit note is needed.
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import order_ops
from store_backend.services import pricing
from store_backend.shop.models import Invoice
from store_backend.shop.models import InvoiceStatus
from store_backend.shop.models import OrderFeeSnapshot

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.order.models import Order

    from store_backend.users.models import User

CANCEL_WINDOW = timedelta(days=8)

NEEDS_CANCEL = "cancel_invoice"
NEEDS_CREDIT_NOTE = "credit_note"


def peek(order: Order) -> Invoice:
    """The Order's invoice record without creating it; unsaved when none exists."""
    return Invoice.objects.filter(order=order).first() or Invoice(order=order)


def cancel_by(invoice: Invoice) -> date | None:
    return invoice.date + CANCEL_WINDOW if invoice.date else None


def needed(invoice: Invoice, *, today: date | None = None) -> str:
    """What an issued invoice of a cancelled or returned Order still needs."""
    today = today or timezone.localdate()
    deadline = cancel_by(invoice)
    return NEEDS_CANCEL if deadline and today <= deadline else NEEDS_CREDIT_NOTE


def _fields(invoice: Invoice) -> dict:
    return {
        "status": invoice.status,
        "number": invoice.number,
        "date": str(invoice.date) if invoice.date else None,
        "credit_note_number": invoice.credit_note_number,
        "credit_note_date": (
            str(invoice.credit_note_date) if invoice.credit_note_date else None
        ),
    }


def _invalid(fields: dict[str, str]) -> UnprocessableError:
    return UnprocessableError(
        "Fatura bilgisi geçersiz.",
        fields={name: [message] for name, message in fields.items()},
    )


def _validate(
    status: str,
    number: str,
    on: date | None,
    credit_number: str,
    credit_on: date | None,
) -> None:
    errors: dict[str, str] = {}
    required = "Bu alan zorunludur."
    if status == InvoiceStatus.NOT_ISSUED:
        if number or on or credit_number or credit_on:
            errors["status"] = "Kesilmemiş faturanın bilgisi olamaz."
    else:
        if not number:
            errors["number"] = required
        if on is None:
            errors["date"] = required
        elif on > timezone.localdate():
            errors["date"] = "Fatura tarihi gelecekte olamaz."
        if bool(credit_number) != (credit_on is not None):
            errors["credit_note_date" if credit_number else "credit_note_number"] = (
                required
            )
        elif credit_on and on and credit_on < on:
            errors["credit_note_date"] = "İade faturası, faturadan önce olamaz."
    if errors:
        raise _invalid(errors)


@transaction.atomic
def record(  # noqa: PLR0913
    actor: User,
    number: str,
    *,
    status: str,
    invoice_number: str = "",
    invoice_date: date | None = None,
    credit_note_number: str = "",
    credit_note_date: date | None = None,
) -> Order:
    """Replace the Order's invoice record with what Staff typed in."""
    order = order_ops.locked(number)
    invoice_number = invoice_number.strip()
    credit_note_number = credit_note_number.strip()
    _validate(
        status,
        invoice_number,
        invoice_date,
        credit_note_number,
        credit_note_date,
    )
    invoice, _ = Invoice.objects.get_or_create(order=order)
    before = _fields(invoice)
    invoice.status = status
    invoice.number = invoice_number
    invoice.date = invoice_date
    invoice.credit_note_number = credit_note_number
    invoice.credit_note_date = credit_note_date
    try:
        with transaction.atomic():
            invoice.save()
    except IntegrityError:
        raise ConflictError(
            "Bu fatura numarası başka bir siparişte kayıtlı.",
            code="invoice_number_taken",
            fields={"number": ["Bu fatura numarası kullanılıyor."]},
        ) from None
    audit.record(
        actor,
        audit.Action.INVOICE_EDIT,
        target=order,
        summary=f"{order.number}: fatura kaydı ({invoice.get_status_display()})",
        before=before,
        after=_fields(invoice),
    )
    return order


# --- the sheet ----------------------------------------------------------------------


def sheet(order: Order) -> dict:
    """Everything the portal form asks for, in the order it asks for it."""
    record = order_ops.peek_record(order)
    address = order.billing_address or order.shipping_address
    lines = [
        {
            "kind": "product",
            "description": _describe(line),
            "quantity": snap.quantity,
            "kdv_rate": snap.kdv_rate,
            "unit_price_excl": _unit_excl(snap),
            "total_excl": snap.total_excl,
            "total_kdv": snap.total_kdv,
            "total_incl": snap.total_incl,
        }
        for line, snap in (
            (line, line.kdv_snapshot)
            for line in sorted(order.lines.all(), key=lambda row: row.pk)
        )
    ]
    lines += [
        {
            "kind": fee.kind,
            "description": fee.get_kind_display(),
            "quantity": 1,
            "kdv_rate": fee.kdv_rate,
            "unit_price_excl": fee.total_excl,
            "total_excl": fee.total_excl,
            "total_kdv": fee.total_kdv,
            "total_incl": fee.total_incl,
        }
        for fee in OrderFeeSnapshot.objects.filter(order=order).order_by("pk")
    ]
    return {
        "order_number": order.number,
        "order_date": order.date_placed,
        "buyer": {
            "name": f"{address.first_name} {address.last_name}".strip()
            if address
            else "",
            "tax_number": record.buyer_tax_number,
            "company_title": record.company_title,
            "il": address.state if address else "",
            "ilce": address.line4 if address else "",
            "mahalle": address.line3 if address else "",
            "street": address.line1 if address else "",
            "postal_code": address.postcode if address else "",
        },
        "lines": lines,
        "total_excl": sum((row["total_excl"] for row in lines), pricing.ZERO),
        "total_kdv": sum((row["total_kdv"] for row in lines), pricing.ZERO),
        "total_incl": sum((row["total_incl"] for row in lines), pricing.ZERO),
    }


def _describe(line) -> str:
    profile = line.product.variant_profile
    return f"{line.product.parent.title} {profile.colour.name} {profile.size}".strip()


def _unit_excl(snap) -> Decimal:
    return pricing.kurus(snap.total_excl / snap.quantity)
