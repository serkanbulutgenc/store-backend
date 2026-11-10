# ruff: noqa: EM101, TRY003
"""Dashboard order operations: payment, cancel, ship, deliver, return, refund, edits.

Fulfilment status is Oscar's `Order.status`; Payment status is derived from the
payment Source (see `tracking.payment_status_of`). Every operation locks the Order
row, checks the transition is legal (409 `illegal_transition` otherwise), applies it,
and writes one audit entry, all in one transaction.

Stock: placement reserves units (`num_allocated`). Cancelling releases the
reservation; shipping consumes it (on-hand and reserved both drop).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from oscar.apps.order.models import Order
from oscar.apps.partner.exceptions import InvalidStockAdjustment
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import checkout
from store_backend.services import outbox
from store_backend.services import tracking
from store_backend.services.checkout import STATUS_NEW
from store_backend.services.checkout import STATUS_PREPARING
from store_backend.services.tracking import PAYMENT_PAID
from store_backend.services.tracking import PAYMENT_REFUNDED
from store_backend.services.tracking import PAYMENT_UNPAID
from store_backend.shop.models import OrderRecord
from store_backend.shop.models import RefundMethod
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet
    from oscar.apps.payment.models import Source

    from store_backend.users.models import User

STATUS_SHIPPED = "shipped"
STATUS_DELIVERED = "delivered"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_RETURN_REQUESTED = "return_requested"
STATUS_RETURNED = "returned"

EDITABLE_STATUSES = (STATUS_NEW, STATUS_PREPARING)
NOT_PAYABLE = (STATUS_CANCELLED, STATUS_RETURN_REQUESTED, STATUS_RETURNED)
MIN_REASON_LENGTH = 3
ZERO = Decimal(0)
TR_IBAN = re.compile(r"TR\d{24}")
TAX_NUMBER = re.compile(r"\d{10}|\d{11}")  # company VKN or personal TCKN
MAX_FUTURE = 24 * 60 * 60  # seconds a recorded payment date may sit in the future
IBAN_MOD = 97

PAYMENT_STATES = (PAYMENT_UNPAID, PAYMENT_PAID, PAYMENT_REFUNDED)


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _illegal(order: Order, action: str) -> ConflictError:
    return ConflictError(
        f"Bu işlem “{order.status}” durumundaki sipariş için yapılamaz.",
        code="illegal_transition",
        fields={"status": [action]},
    )


# --- reading ------------------------------------------------------------------


def _locked(number: str) -> Order:
    order = (
        Order.objects.select_for_update(of=("self",))
        .filter(number=tracking.normalise_number(number))
        .first()
    )
    if order is None:
        raise NotFoundError
    return order


def peek_record(order: Order) -> OrderRecord:
    """The Order's record without creating it; unsaved when nothing was recorded."""
    return OrderRecord.objects.filter(order=order).first() or OrderRecord(order=order)


def record_of(order: Order) -> OrderRecord:
    record, _ = OrderRecord.objects.get_or_create(order=order)
    return record


def source_of(order: Order) -> Source:
    return order.sources.select_for_update().get()


def detail_queryset() -> QuerySet[Order]:
    return Order.objects.select_related(
        "user",
        "shipping_address",
        "billing_address",
    ).prefetch_related(
        "messages",
        "sources__source_type",
        "surcharges",
        "lines__product__parent",
        "lines__product__variant_profile__colour",
    )


def get(number: str) -> Order:
    order = detail_queryset().filter(number=tracking.normalise_number(number)).first()
    if order is None:
        raise NotFoundError
    return order


def search(
    *,
    q: str | None = None,
    status: str | None = None,
    payment_status: str | None = None,
) -> QuerySet[Order]:
    """Orders newest first, filtered by free text, Fulfilment and Payment status."""
    orders = Order.objects.select_related("user", "shipping_address").prefetch_related(
        "sources",
        "lines",
    )
    if status:
        orders = orders.filter(status=status)
    if payment_status == PAYMENT_REFUNDED:
        orders = orders.filter(sources__amount_refunded__gt=0)
    elif payment_status == PAYMENT_PAID:
        orders = orders.filter(
            sources__amount_debited__gt=0,
            sources__amount_refunded=0,
        )
    elif payment_status == PAYMENT_UNPAID:
        orders = orders.filter(sources__amount_debited=0, sources__amount_refunded=0)
    if q and q.strip():
        term = q.strip()
        orders = orders.filter(
            Q(number__icontains=term)
            | Q(guest_email__icontains=term)
            | Q(user__email__icontains=term)
            | Q(shipping_address__last_name__icontains=term)
            | Q(shipping_address__first_name__icontains=term),
        )
    return orders.order_by("-date_placed", "-pk")


def available_actions(order: Order) -> list[str]:
    """The transitions that are legal right now (permissions are checked separately)."""
    status = order.status
    payment = tracking.payment_status_of(order)
    actions: list[str] = []
    if payment == PAYMENT_UNPAID and status not in NOT_PAYABLE:
        actions.append("mark_paid")
    if status in EDITABLE_STATUSES:
        actions += ["cancel", "edit_shipping"]
    if status == STATUS_PREPARING:
        actions.append("ship")
    if status == STATUS_SHIPPED:
        actions.append("deliver")
    if status in (STATUS_DELIVERED, STATUS_COMPLETED):
        actions.append("request_return")
    if status == STATUS_RETURN_REQUESTED:
        actions.append("mark_returned")
    if payment == PAYMENT_PAID and status in (STATUS_RETURNED, STATUS_CANCELLED):
        actions.append("refund")
    return actions


# --- stock --------------------------------------------------------------------


def _locked_stock(order: Order) -> list[tuple[StockRecord, int]]:
    """(stock record, quantity) for each line, records locked in primary-key order."""
    quantities: dict[int, int] = {}
    for line in order.lines.all():
        if line.stockrecord_id is not None:
            quantities[line.stockrecord_id] = (
                quantities.get(line.stockrecord_id, 0) + line.quantity
            )
    records = StockRecord.objects.select_for_update().filter(pk__in=quantities)
    return [(r, quantities[r.pk]) for r in records.order_by("pk")]


def _release_stock(order: Order) -> None:
    for record, quantity in _locked_stock(order):
        record.cancel_allocation(quantity)


def _consume_stock(order: Order) -> None:
    for record, quantity in _locked_stock(order):
        try:
            record.consume_allocation(quantity)
        except InvalidStockAdjustment:
            raise ConflictError(
                "Stok kaydı bu siparişi göndermek için yetersiz. Stoğu kontrol edin.",
                code="stock_mismatch",
            ) from None


# --- payment ------------------------------------------------------------------


def _snapshot(order: Order) -> dict:
    return {"status": order.status, "payment_status": tracking.payment_status_of(order)}


def _debit(order: Order, record: OrderRecord, amount: Decimal, when: datetime) -> None:
    source_of(order).debit(amount)
    record.paid_amount = amount
    record.paid_at = when


@transaction.atomic
def mark_paid(
    actor: User,
    number: str,
    *,
    amount: Decimal,
    paid_at: datetime,
) -> Order:
    """Record that the money arrived. A new bank-transfer Order moves to preparing."""
    order = _locked(number)
    if order.status in NOT_PAYABLE or (
        tracking.payment_status_of(order) != PAYMENT_UNPAID
    ):
        raise _illegal(order, "mark_paid")
    if amount != order.total_incl_tax:
        raise _invalid(
            "amount",
            f"Tutar sipariş toplamına ({order.total_incl_tax} TL) eşit olmalı.",
        )
    if timezone.is_naive(paid_at):
        paid_at = timezone.make_aware(paid_at)
    if (paid_at - timezone.now()).total_seconds() > MAX_FUTURE:
        raise _invalid("paid_at", "Ödeme tarihi gelecekte olamaz.")
    before = _snapshot(order)
    record = record_of(order)
    _debit(order, record, amount, paid_at)
    record.save(update_fields=["paid_amount", "paid_at"])
    if order.status == STATUS_NEW:
        order.status = STATUS_PREPARING
        order.save(update_fields=["status"])
    audit.record(
        actor,
        audit.Action.MARK_PAID,
        target=order,
        summary=f"{order.number}: {amount} TL ödendi",
        before=before,
        after={
            **_snapshot(order),
            "amount": str(amount),
            "paid_at": paid_at.isoformat(),
        },
    )
    return order


@transaction.atomic
def cancel(actor: User, number: str, *, reason: str) -> Order:
    """Cancel before shipping and release the reserved stock.

    A paid Order can only be cancelled by someone allowed to (the Owner); the money
    then waits for a recorded refund.
    """
    order = _locked(number)
    if order.status not in EDITABLE_STATUSES:
        raise _illegal(order, "cancel")
    reason = reason.strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise _invalid("reason", "Bir neden yazın.")
    paid = tracking.payment_status_of(order) != PAYMENT_UNPAID
    if paid and not actor.has_perm(Perm.CANCEL_PAID_ORDERS.permission_name):
        raise ForbiddenError(
            "Ödenmiş siparişi yalnızca mağaza sahibi iptal edebilir.",
            code="owner_only",
        )
    before = _snapshot(order)
    _release_stock(order)
    order.status = STATUS_CANCELLED
    order.save(update_fields=["status"])
    record = record_of(order)
    record.cancel_reason = reason
    record.cancelled_at = timezone.now()
    record.save(update_fields=["cancel_reason", "cancelled_at"])
    audit.record(
        actor,
        audit.Action.CANCEL,
        target=order,
        summary=f"{order.number}: iptal ({reason})",
        before=before,
        after={**_snapshot(order), "reason": reason},
    )
    return order


@transaction.atomic
def ship(actor: User, number: str, *, carrier: str, tracking_number: str) -> Order:
    """Hand the parcel to the carrier: consumes the reserved stock."""
    order = _locked(number)
    if order.status != STATUS_PREPARING:
        raise _illegal(order, "ship")
    carrier, tracking_number = carrier.strip(), tracking_number.strip()
    errors = {
        name: ["Bu alan zorunludur."]
        for name, value in (("carrier", carrier), ("tracking_number", tracking_number))
        if not value
    }
    if errors:
        raise UnprocessableError(fields=errors)
    before = _snapshot(order)
    _consume_stock(order)
    order.status = STATUS_SHIPPED
    order.save(update_fields=["status"])
    record = record_of(order)
    record.carrier = carrier
    record.tracking_number = tracking_number
    record.shipped_at = timezone.now()
    record.save(update_fields=["carrier", "tracking_number", "shipped_at"])
    audit.record(
        actor,
        audit.Action.SHIP,
        target=order,
        summary=f"{order.number}: {carrier} {tracking_number}",
        before=before,
        after={**_snapshot(order), "carrier": carrier, "tracking": tracking_number},
    )
    return order


@transaction.atomic
def deliver(actor: User, number: str) -> Order:
    """Mark delivered. Cash on delivery is recorded as paid in full now."""
    order = _locked(number)
    if order.status != STATUS_SHIPPED:
        raise _illegal(order, "deliver")
    before = _snapshot(order)
    now = timezone.now()
    record = record_of(order)
    if tracking.payment_status_of(order) == PAYMENT_UNPAID:
        _debit(order, record, order.total_incl_tax, now)
    record.delivered_at = now
    record.save(update_fields=["paid_amount", "paid_at", "delivered_at"])
    order.status = STATUS_DELIVERED
    order.save(update_fields=["status"])
    audit.record(
        actor,
        audit.Action.DELIVER,
        target=order,
        summary=f"{order.number}: teslim edildi",
        before=before,
        after=_snapshot(order),
    )
    return order


def _move(  # noqa: PLR0913
    actor: User,
    number: str,
    *,
    allowed_from: tuple[str, ...],
    to: str,
    action: audit.Action,
    stamp: str,
    summary: str,
) -> Order:
    order = _locked(number)
    if order.status not in allowed_from:
        raise _illegal(order, str(action))
    before = _snapshot(order)
    order.status = to
    order.save(update_fields=["status"])
    record = record_of(order)
    setattr(record, stamp, timezone.now())
    record.save(update_fields=[stamp])
    audit.record(
        actor,
        action,
        target=order,
        summary=f"{order.number}: {summary}",
        before=before,
        after=_snapshot(order),
    )
    return order


@transaction.atomic
def request_return(actor: User, number: str) -> Order:
    return _move(
        actor,
        number,
        allowed_from=(STATUS_DELIVERED, STATUS_COMPLETED),
        to=STATUS_RETURN_REQUESTED,
        action=audit.Action.RETURN_REQUEST,
        stamp="return_requested_at",
        summary="iade talebi",
    )


@transaction.atomic
def mark_returned(actor: User, number: str) -> Order:
    return _move(
        actor,
        number,
        allowed_from=(STATUS_RETURN_REQUESTED,),
        to=STATUS_RETURNED,
        action=audit.Action.RETURN_RECEIVED,
        stamp="returned_at",
        summary="iade teslim alındı",
    )


def clean_iban(raw: str) -> str:
    iban = re.sub(r"\s+", "", raw).upper()
    msg = "Geçerli bir IBAN girin."
    if not TR_IBAN.fullmatch(iban):
        raise _invalid("iban", msg)
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    if int(digits) % IBAN_MOD != 1:
        raise _invalid("iban", msg)
    return iban


@transaction.atomic
def refund(
    actor: User,
    number: str,
    *,
    amount: Decimal,
    method: RefundMethod,
    iban: str = "",
) -> Order:
    """Record a refund for a paid Order that was returned or cancelled."""
    order = _locked(number)
    if tracking.payment_status_of(order) != PAYMENT_PAID or order.status not in (
        STATUS_RETURNED,
        STATUS_CANCELLED,
    ):
        raise _illegal(order, "refund")
    source = source_of(order)
    if amount <= ZERO or amount > source.amount_debited:
        raise _invalid("amount", "İade tutarı ödenen tutarı aşamaz.")
    clean = clean_iban(iban) if method == RefundMethod.BANK_TRANSFER else ""
    before = _snapshot(order)
    source.refund(amount)
    record = record_of(order)
    record.refund_amount = amount
    record.refund_method = method
    record.refund_iban = clean
    record.refunded_at = timezone.now()
    record.save(
        update_fields=["refund_amount", "refund_method", "refund_iban", "refunded_at"],
    )
    audit.record(
        actor,
        audit.Action.REFUND,
        target=order,
        summary=f"{order.number}: {amount} TL iade ({method.label})",
        before=before,
        after={**_snapshot(order), "amount": str(amount), "method": str(method)},
    )
    return order


# --- edits --------------------------------------------------------------------

ADDRESS_FIELDS = ("il", "ilce", "mahalle", "street", "postal_code")
_ADDRESS_COLUMNS = {
    "street": "line1",
    "mahalle": "line3",
    "ilce": "line4",
    "il": "state",
    "postal_code": "postcode",
}


def shipping_fields(order: Order) -> dict[str, str]:
    address = order.shipping_address
    as_e164 = getattr(address.phone_number, "as_e164", "") if address else ""
    values = {
        field: getattr(address, column, "") or ""
        for field, column in _ADDRESS_COLUMNS.items()
    }
    return {**values, "phone": as_e164 or ""}


@transaction.atomic
def edit_shipping(actor: User, number: str, **changes: str) -> Order:
    """Change the delivery address and/or phone while the Order is new or preparing."""
    order = _locked(number)
    if order.status not in EDITABLE_STATUSES:
        raise _illegal(order, "edit_shipping")
    address = order.shipping_address
    if address is None or not changes:
        raise _invalid("address", "Değiştirilecek bilgi gönderilmedi.")
    before = shipping_fields(order)
    merged = {**before, **changes}
    clean = checkout.clean_address(
        "shipping_address",
        checkout.Address(**{f: merged[f] for f in ADDRESS_FIELDS}),
    )
    for field in ADDRESS_FIELDS:
        setattr(address, _ADDRESS_COLUMNS[field], getattr(clean, field))
    if "phone" in changes:
        address.phone_number = checkout.clean_phone(changes["phone"])
    address.save()
    address.refresh_from_db()  # the phone is a plain string until reloaded
    after = shipping_fields(order)
    audit.record(
        actor,
        audit.Action.ORDER_EDIT,
        target=order,
        summary=f"{order.number}: teslimat bilgisi düzeltildi",
        before=before,
        after=after,
    )
    return order


@transaction.atomic
def correct_email(actor: User, number: str, *, email: str) -> Order:
    """Fix the Shopper's email. Resending the order email is wired in the messaging
    work (ticket 30); until then this only records the correction."""
    order = _locked(number)
    email = email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise _invalid("email", "Geçerli bir e-posta adresi girin.") from None
    before = {"email": order.guest_email or (order.user.email if order.user else "")}
    order.guest_email = email
    order.save(update_fields=["guest_email"])
    audit.record(
        actor,
        audit.Action.ORDER_EDIT,
        target=order,
        summary=f"{order.number}: e-posta düzeltildi",
        before=before,
        after={"email": email},
    )
    return order


@transaction.atomic
def resend_message(actor: User, number: str, message_id: int) -> Order:
    order = _locked(number)
    copy = outbox.resend(message_id, order=order)
    audit.record(
        actor,
        audit.Action.MESSAGE_RESEND,
        target=order,
        summary=f"{order.number}: e-posta yeniden gönderildi ({copy.template})",
        before={"message": message_id},
        after={"message": copy.pk},
    )
    return order


@transaction.atomic
def set_invoice_details(actor: User, number: str, **changes: str) -> Order:
    """Add the buyer's tax number and/or company title when they ask for it."""
    order = _locked(number)
    if not changes:
        raise _invalid("buyer_tax_number", "Değiştirilecek bilgi gönderilmedi.")
    record = record_of(order)
    before = {
        "buyer_tax_number": record.buyer_tax_number,
        "company_title": record.company_title,
    }
    clean = {k: " ".join(v.split()) for k, v in changes.items()}
    tax = clean.get("buyer_tax_number")
    if tax and not TAX_NUMBER.fullmatch(tax):
        raise _invalid(
            "buyer_tax_number",
            "Vergi numarası 10 haneli, T.C. kimlik numarası 11 haneli olmalı.",
        )
    for field, value in clean.items():
        setattr(record, field, value)
    record.save(update_fields=list(clean))
    audit.record(
        actor,
        audit.Action.ORDER_EDIT,
        target=order,
        summary=f"{order.number}: fatura bilgisi güncellendi",
        before=before,
        after={
            "buyer_tax_number": record.buyer_tax_number,
            "company_title": record.company_title,
        },
    )
    return order
