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

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import contact
from store_backend.services import order_context
from store_backend.services import outbox
from store_backend.services import pre_information
from store_backend.services import stock
from store_backend.services import tracking
from store_backend.services.checkout import STATUS_NEW
from store_backend.services.checkout import STATUS_PREPARING
from store_backend.services.tracking import PAYMENT_PAID
from store_backend.services.tracking import PAYMENT_REFUNDED
from store_backend.services.tracking import PAYMENT_UNPAID
from store_backend.shop.models import Carrier
from store_backend.shop.models import OrderRecord
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import RefundMethod
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet
    from oscar.apps.payment.models import Source

    from store_backend.users.models import User

logger = logging.getLogger(__name__)

STATUS_SHIPPED = "shipped"
STATUS_DELIVERED = "delivered"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_RETURN_REQUESTED = "return_requested"
STATUS_RETURNED = "returned"

EDITABLE_STATUSES = (STATUS_NEW, STATUS_PREPARING)
PAYABLE = (
    STATUS_NEW,
    STATUS_PREPARING,
    STATUS_SHIPPED,
    STATUS_DELIVERED,
    STATUS_COMPLETED,
)
MIN_REASON_LENGTH = 3
ZERO = Decimal(0)
TR_IBAN = re.compile(r"TR\d{24}")
TAX_NUMBER = re.compile(r"\d{10}|\d{11}")  # company VKN or personal TCKN
MAX_FUTURE = 24 * 60 * 60  # seconds a recorded payment date may sit in the future
IBAN_MOD = 97

PAYMENT_STATES = (PAYMENT_UNPAID, PAYMENT_PAID, PAYMENT_REFUNDED)


@dataclass(frozen=True)
class Rule:
    """When an operation is legal: the Fulfilment and Payment states it accepts."""

    statuses: tuple[str, ...]
    payments: tuple[str, ...] = PAYMENT_STATES

    def allows(self, order: Order) -> bool:
        if order.status not in self.statuses:
            return False
        # The Payment status costs a query, so ask for it only when the rule cares.
        return (
            self.payments == PAYMENT_STATES
            or tracking.payment_status_of(order) in self.payments
        )


# The one place that says which operation is legal in which state. `available_actions`
# lists the ones that hold right now; every operation guards itself with `_guard`.
RULES: dict[str, Rule] = {
    "mark_paid": Rule(PAYABLE, (PAYMENT_UNPAID,)),
    "cancel": Rule(EDITABLE_STATUSES),
    "edit_shipping": Rule(EDITABLE_STATUSES),
    "ship": Rule((STATUS_PREPARING,)),
    "deliver": Rule((STATUS_SHIPPED,)),
    "request_return": Rule((STATUS_DELIVERED, STATUS_COMPLETED)),
    "mark_returned": Rule((STATUS_RETURN_REQUESTED,)),
    "refund": Rule((STATUS_RETURNED, STATUS_CANCELLED), (PAYMENT_PAID,)),
}


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _guard(order: Order, action: str, *, named: str | None = None) -> None:
    """409 `illegal_transition` unless `action` is legal for this Order right now.

    The error names the action as `named` (the audit action's name, for returns).
    """
    if not RULES[action].allows(order):
        msg = f"Bu işlem “{order.status}” durumundaki sipariş için yapılamaz."
        raise ConflictError(
            msg,
            code="illegal_transition",
            fields={"status": [named or action]},
        )


# --- reading ------------------------------------------------------------------


def locked(number: str) -> Order:
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
    return [action for action, rule in RULES.items() if rule.allows(order)]


# --- payment ------------------------------------------------------------------


def _snapshot(order: Order) -> dict:
    return {"status": order.status, "payment_status": tracking.payment_status_of(order)}


def _apply(  # noqa: PLR0913
    actor: User | None,
    order: Order,
    action: audit.Action,
    *,
    summary: str,
    before: dict,
    to: str | None = None,
    stamps: dict | None = None,
    extra: dict | None = None,
) -> None:
    """Commit one transition: new status, stamps on the record, one audit entry."""
    if to is not None:
        order.status = to
        order.save(update_fields=["status"])
    if stamps:
        record = record_of(order)
        for field, value in stamps.items():
            setattr(record, field, value)
        record.save(update_fields=list(stamps))
    audit.record(
        actor,
        action,
        target=order,
        summary=f"{order.number}: {summary}",
        before=before,
        after={**_snapshot(order), **(extra or {})},
    )


@transaction.atomic
def mark_paid(
    actor: User,
    number: str,
    *,
    amount: Decimal,
    paid_at: datetime,
) -> Order:
    """Record that the money arrived. A new bank-transfer Order moves to preparing."""
    order = locked(number)
    _guard(order, "mark_paid")
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
    source_of(order).debit(amount)
    _apply(
        actor,
        order,
        audit.Action.MARK_PAID,
        summary=f"{amount} TL ödendi",
        before=before,
        to=STATUS_PREPARING if order.status == STATUS_NEW else None,
        stamps={"paid_amount": amount, "paid_at": paid_at},
        extra={"amount": str(amount), "paid_at": paid_at.isoformat()},
    )
    outbox.notify_shopper(order, "order_paid")
    return order


def _cancel_order(
    actor: User | None,
    order: Order,
    *,
    reason: str,
    summary: str,
    at: datetime | None = None,
) -> None:
    """Cancel and release the reserved stock; a person's cancel and the timed one."""
    before = _snapshot(order)
    stock.release(order)
    _apply(
        actor,
        order,
        audit.Action.CANCEL,
        summary=summary,
        before=before,
        to=STATUS_CANCELLED,
        stamps={"cancel_reason": reason, "cancelled_at": at or timezone.now()},
        extra={"reason": reason},
    )


@transaction.atomic
def cancel(actor: User, number: str, *, reason: str) -> Order:
    """Cancel before shipping and release the reserved stock.

    A paid Order can only be cancelled by someone allowed to (the Owner); the money
    then waits for a recorded refund.
    """
    order = locked(number)
    _guard(order, "cancel")
    reason = reason.strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise _invalid("reason", "Bir neden yazın.")
    paid = tracking.payment_status_of(order) != PAYMENT_UNPAID
    if paid and not actor.has_perm(Perm.CANCEL_PAID_ORDERS.permission_name):
        raise ForbiddenError(
            "Ödenmiş siparişi yalnızca mağaza sahibi iptal edebilir.",
            code="owner_only",
        )
    _cancel_order(actor, order, reason=reason, summary=f"iptal ({reason})")
    outbox.notify_shopper(order, "order_cancelled", reason=reason)
    return order


SHOPPER_CANCEL_REASON = "Müşteri iptal etti"


@transaction.atomic
def cancel_by_shopper(number: str) -> Order:
    """The Shopper cancels their own Order before it ships; stock is released.

    The caller has already checked that the Shopper may act on this Order. A paid
    Order's money waits for a refund recorded by the Owner.
    """
    order = locked(number)
    _guard(order, "cancel")
    _cancel_order(None, order, reason=SHOPPER_CANCEL_REASON, summary="iptal (müşteri)")
    outbox.notify_shopper(order, "order_cancelled", reason=SHOPPER_CANCEL_REASON)
    return order


def carrier_names() -> list[str]:
    """The shop's carriers in tie-break order; an Order ships with one of them."""
    return list(Carrier.objects.values_list("name", flat=True))


@transaction.atomic
def ship(actor: User, number: str, *, carrier: str, tracking_number: str) -> Order:
    """Hand the parcel to the carrier: consumes the reserved stock."""
    order = locked(number)
    _guard(order, "ship")
    carrier, tracking_number = carrier.strip(), tracking_number.strip()
    errors = {
        name: ["Bu alan zorunludur."]
        for name, value in (("carrier", carrier), ("tracking_number", tracking_number))
        if not value
    }
    if not errors:
        known = {name.casefold(): name for name in carrier_names()}
        if carrier.casefold() not in known:
            msg = "Listeden bir kargo firması seçin."
            raise UnprocessableError(msg, fields={"carrier": [msg]})
        carrier = known[carrier.casefold()]
    if errors:
        raise UnprocessableError(fields=errors)
    before = _snapshot(order)
    stock.consume(order)
    _apply(
        actor,
        order,
        audit.Action.SHIP,
        summary=f"{carrier} {tracking_number}",
        before=before,
        to=STATUS_SHIPPED,
        stamps={
            "carrier": carrier,
            "tracking_number": tracking_number,
            "shipped_at": timezone.now(),
        },
        extra={"carrier": carrier, "tracking": tracking_number},
    )
    outbox.notify_shopper(
        order,
        "order_shipped",
        carrier=carrier,
        tracking_number=tracking_number,
    )
    return order


@transaction.atomic
def deliver(actor: User, number: str) -> Order:
    """Mark delivered. Cash on delivery is recorded as paid in full now."""
    order = locked(number)
    _guard(order, "deliver")
    before = _snapshot(order)
    now = timezone.now()
    stamps: dict = {"delivered_at": now}
    if tracking.payment_status_of(order) == PAYMENT_UNPAID:
        source_of(order).debit(order.total_incl_tax)
        stamps |= {"paid_amount": order.total_incl_tax, "paid_at": now}
    _apply(
        actor,
        order,
        audit.Action.DELIVER,
        summary="teslim edildi",
        before=before,
        to=STATUS_DELIVERED,
        stamps=stamps,
    )
    return order


def _refuse_while_requests_open(order: Order) -> None:
    """Staff's Order-level return and the Shopper's line requests must not disagree."""
    from store_backend.shop.models import ReturnStatus  # noqa: PLC0415

    open_states = (ReturnStatus.REQUESTED, ReturnStatus.APPROVED)
    if order.return_requests.filter(status__in=open_states).exists():
        msg = "Bu siparişte açık bir iade talebi var; önce onu sonuçlandırın."
        raise ConflictError(msg, code="open_return_request")


@transaction.atomic
def request_return(actor: User, number: str) -> Order:
    order = locked(number)
    _guard(order, "request_return", named=str(audit.Action.RETURN_REQUEST))
    _refuse_while_requests_open(order)
    _apply(
        actor,
        order,
        audit.Action.RETURN_REQUEST,
        summary="iade talebi",
        before=_snapshot(order),
        to=STATUS_RETURN_REQUESTED,
        stamps={"return_requested_at": timezone.now()},
    )
    return order


@transaction.atomic
def mark_returned(actor: User, number: str) -> Order:
    order = locked(number)
    _guard(order, "mark_returned", named=str(audit.Action.RETURN_RECEIVED))
    _refuse_while_requests_open(order)
    _apply(
        actor,
        order,
        audit.Action.RETURN_RECEIVED,
        summary="iade teslim alındı",
        before=_snapshot(order),
        to=STATUS_RETURNED,
        stamps={"returned_at": timezone.now()},
    )
    outbox.notify_shopper(order, "return_received")
    return order


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
    order = locked(number)
    _guard(order, "refund")
    source = source_of(order)
    if amount <= ZERO or amount > source.amount_debited - source.amount_refunded:
        raise _invalid("amount", "İade tutarı ödenen tutarı aşamaz.")
    clean = clean_iban(iban) if method == RefundMethod.BANK_TRANSFER else ""
    before = _snapshot(order)
    source.refund(amount)
    _apply(
        actor,
        order,
        audit.Action.REFUND,
        summary=f"{amount} TL iade ({method.label})",
        before=before,
        stamps={
            "refund_amount": amount,
            "refund_method": method,
            "refund_iban": clean,
            "refunded_at": timezone.now(),
        },
        extra={"amount": str(amount), "method": str(method)},
    )
    outbox.notify_shopper(
        order,
        "refund_issued",
        amount=pre_information.format_try(amount),
        method=method.label,
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
    order = locked(number)
    _guard(order, "edit_shipping")
    address = order.shipping_address
    if address is None or not changes:
        raise _invalid("address", "Değiştirilecek bilgi gönderilmedi.")
    before = shipping_fields(order)
    merged = {**before, **changes}
    clean = contact.clean_address(
        "shipping_address",
        contact.Address(**{f: merged[f] for f in ADDRESS_FIELDS}),
    )
    for field in ADDRESS_FIELDS:
        setattr(address, _ADDRESS_COLUMNS[field], getattr(clean, field))
    if "phone" in changes:
        address.phone_number = contact.clean_phone(changes["phone"])
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
    """Fix the Shopper's email and send the order email to the corrected address."""
    order = locked(number)
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
    outbox.notify_shopper(order, "order_placed")
    return order


@transaction.atomic
def resend_message(actor: User, number: str, message_id: int) -> Order:
    order = locked(number)
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
    order = locked(number)
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


# --- timed jobs ---------------------------------------------------------------

REMINDER_AFTER = timedelta(hours=24)
AUTO_CANCEL_REASON = "Ödeme süresi (48 saat) içinde ödeme alınamadı."


@contextmanager
def _isolated(order: Order):
    """One Order's work in its own transaction; its failure never stops the batch."""
    try:
        with transaction.atomic():
            yield
    except Exception:
        logger.exception("Timed job failed for order %s", order.number)


def unpaid_transfers(placed_before: datetime) -> QuerySet[Order]:
    return Order.objects.filter(
        status=STATUS_NEW,
        date_placed__lte=placed_before,
        sources__source_type__code=PaymentMethod.BANK_TRANSFER,
    ).distinct()


def send_payment_reminders(*, now: datetime | None = None) -> int:
    """One reminder per unpaid bank-transfer Order, once 24 h have passed."""
    now = now or timezone.now()
    sent = 0
    due = unpaid_transfers(now - REMINDER_AFTER).filter(
        date_placed__gt=now
        - order_context.PAYMENT_WINDOW,  # past it, the cancel job acts
    )
    for order in due.exclude(messages__template="payment_reminder"):
        with _isolated(order):
            order = locked(order.number)  # noqa: PLW2901
            if (
                order.status != STATUS_NEW
                or tracking.payment_status_of(order) != PAYMENT_UNPAID
                or order.messages.filter(template="payment_reminder").exists()
            ):
                continue
            outbox.notify_shopper(order, "payment_reminder")
            sent += 1
    return sent


def cancel_unpaid_transfers(*, now: datetime | None = None) -> int:
    """Cancel bank-transfer Orders still unpaid after the 48 h payment window."""
    now = now or timezone.now()
    cancelled = 0
    for order in unpaid_transfers(now - order_context.PAYMENT_WINDOW):
        with _isolated(order):
            order = locked(order.number)  # noqa: PLW2901
            if order.status != STATUS_NEW or (
                tracking.payment_status_of(order) != PAYMENT_UNPAID
            ):
                continue
            _cancel_order(
                None,
                order,
                reason=AUTO_CANCEL_REASON,
                summary="ödeme süresi doldu, otomatik iptal",
                at=now,
            )
            outbox.notify_shopper(order, "order_auto_cancelled")
            cancelled += 1
    return cancelled
