# ruff: noqa: EM101, EM102, TRY003
"""Shopper return requests: which lines of a delivered Order may come back.

A request is its own record (`ReturnRequest` with `ReturnLine`s) and never touches the
Order's Fulfilment status, so a partial return leaves the Order delivered. Staff's
whole-Order `return_requested` and `returned` statuses in `order_ops` stay as they are.
A request must come within 14 days of delivery, may not include lines of a hygiene
Product class, and may not ask for more of a line than was bought and not already
asked for (a rejected request frees its quantity again).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import order_ops
from store_backend.services import outbox
from store_backend.services import pre_information
from store_backend.services import stock
from store_backend.services import tracking
from store_backend.shop.models import RefundMethod
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnReason
from store_backend.shop.models import ReturnRequest
from store_backend.shop.models import ReturnStatus

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from django.db.models import QuerySet
    from oscar.apps.order.models import Line
    from oscar.apps.order.models import Order

    from store_backend.users.models import User

RETURN_WINDOW = timedelta(days=14)
RETURNABLE_STATUSES = (order_ops.STATUS_DELIVERED, order_ops.STATUS_COMPLETED)
NOTE_MAX = 500
MIN_REASON_LENGTH = 3
# The shop pays the return carrier when the fault is the shop's.
SHOP_PAYS_SHIPPING = (ReturnReason.DEFECTIVE, ReturnReason.WRONG_ITEM)


@dataclass(frozen=True)
class Item:
    line_id: int
    quantity: int


def deadline(order: Order) -> datetime | None:
    """The last moment a return may be requested, or None if it never will be."""
    if order.status not in RETURNABLE_STATUSES:
        return None
    delivered_at = order_ops.peek_record(order).delivered_at
    return delivered_at + RETURN_WINDOW if delivered_at else None


def _is_open(order: Order, now: datetime) -> bool:
    last = deadline(order)
    return last is not None and now <= last


def is_hygiene(line: Line) -> bool:
    return line.product.parent.product_class.profile.is_hygiene


def _already_asked(order: Order) -> dict[int, int]:
    rows = (
        ReturnLine.objects.filter(request__order=order)
        .exclude(request__status=ReturnStatus.REJECTED)
        .values("order_line_id")
        .annotate(total=Sum("quantity"))
    )
    return {row["order_line_id"]: row["total"] for row in rows}


def returnable_quantities(
    order: Order,
    *,
    now: datetime | None = None,
) -> dict[int, int]:
    """Per Order line: how many units could still be requested right now."""
    open_ = _is_open(order, now or timezone.now())
    asked = _already_asked(order)
    return {
        line.pk: (
            max(line.quantity - asked.get(line.pk, 0), 0)
            if open_ and not is_hygiene(line)
            else 0
        )
        for line in order.lines.select_related(
            "product__parent__product_class__profile",
        )
    }


def _invalid_lines(message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={"lines": [message]})


def _check_items(order: Order, items: list[Item], now: datetime) -> dict[int, Line]:
    if order.status not in RETURNABLE_STATUSES:
        raise ConflictError(
            "İade talebi yalnızca teslim edilmiş siparişler için yapılabilir.",
            code="return_not_available",
        )
    if not _is_open(order, now):
        raise ConflictError(
            "İade süresi (teslimattan sonra 14 gün) dolmuş.",
            code="return_window_closed",
        )
    if not items:
        raise _invalid_lines("İade edilecek en az bir ürün seçin.")
    if len({item.line_id for item in items}) != len(items):
        raise _invalid_lines("Aynı ürün birden fazla kez seçilemez.")
    lines = {
        line.pk: line
        for line in order.lines.select_related(
            "product__parent__product_class__profile",
        )
    }
    available = returnable_quantities(order, now=now)
    for item in items:
        line = lines.get(item.line_id)
        if line is None:
            raise _invalid_lines("Seçilen ürün bu siparişte yok.")
        if is_hygiene(line):
            raise _invalid_lines(
                f"{line.title} hijyen ürünü olduğu için iade edilemez.",
            )
        if item.quantity < 1 or item.quantity > available[line.pk]:
            raise _invalid_lines(
                f"{line.title} için en fazla {available[line.pk]} adet "
                "iade talep edebilirsiniz.",
            )
    return lines


@transaction.atomic
def request_return(
    number: str,
    items: list[Item],
    *,
    reason: str,
    note: str = "",
    now: datetime | None = None,
) -> ReturnRequest:
    """File a return request for the chosen lines of a delivered Order.

    The caller has already checked that the Shopper may act on this Order.
    """
    now = now or timezone.now()
    order = order_ops.locked(number)
    if reason not in ReturnReason.values:
        raise UnprocessableError(
            "Bir iade nedeni seçin.",
            fields={"reason": ["Bir iade nedeni seçin."]},
        )
    note = note.strip()[:NOTE_MAX]
    _check_items(order, items, now)
    request = ReturnRequest.objects.create(order=order, reason=reason, note=note)
    ReturnLine.objects.bulk_create(
        ReturnLine(request=request, order_line_id=i.line_id, quantity=i.quantity)
        for i in items
    )
    audit.record(
        None,
        audit.Action.RETURN_REQUEST,
        target=order,
        summary=f"{order.number}: iade talebi (müşteri, {reason})",
        before=None,
        after={
            "return_request": request.pk,
            "lines": {str(i.line_id): i.quantity for i in items},
        },
    )
    context = _email_context(request)
    outbox.notify_shopper(order, "return_requested", **context)
    outbox.notify_staff(
        "staff_return_request",
        {
            "number": order.number,
            "admin_url": outbox.admin_url(f"orders/{order.number}"),
            **context,
        },
    )
    return request


def search(*, status: str | None = None) -> QuerySet[ReturnRequest]:
    """Return requests, oldest first when waiting for a decision, else newest first."""
    if status and status not in ReturnStatus.values:
        msg = "Geçersiz iade durumu."
        raise UnprocessableError(msg, fields={"status": [msg]})
    requests = ReturnRequest.objects.select_related(
        "order__shipping_address",
    ).prefetch_related("lines")
    if status:
        requests = requests.filter(status=status)
    if status == ReturnStatus.REQUESTED:
        return requests.order_by("requested_at", "pk")
    return requests.order_by("-requested_at", "-pk")


def awaiting_decision() -> QuerySet[ReturnRequest]:
    """Requests no Staff member has approved or rejected yet, oldest first."""
    return search(status=ReturnStatus.REQUESTED)


# --- Staff review ----------------------------------------------------------------


def _email_context(request: ReturnRequest) -> dict[str, Any]:
    """What the return emails say about a request: reason, lines, who pays shipping."""
    lines = request.lines.select_related(
        "order_line__product__parent",
        "order_line__product__variant_profile__colour",
    ).order_by("pk")
    return {
        "reason_label": ReturnReason(request.reason).label,
        "reject_reason": request.reject_reason,
        "shop_pays_shipping": request.reason in SHOP_PAYS_SHIPPING,
        "return_lines": [
            {
                "title": row.order_line.product.parent.title,
                "colour": row.order_line.product.variant_profile.colour.name,
                "size": row.order_line.product.variant_profile.size,
                "quantity": row.quantity,
            }
            for row in lines
        ],
    }


def _locked_request(order: Order, request_id: int) -> ReturnRequest:
    request = (
        ReturnRequest.objects.select_for_update()
        .filter(pk=request_id, order=order)
        .first()
    )
    if request is None:
        raise NotFoundError
    return request


def _expect(request: ReturnRequest, *statuses: ReturnStatus, step: str) -> None:
    if request.status not in statuses:
        raise ConflictError(
            f"Bu işlem “{ReturnStatus(request.status).label}” durumundaki iade "
            "talebi için yapılamaz.",
            code="illegal_transition",
            fields={"status": [step]},
        )


def _step(  # noqa: PLR0913
    actor: User,
    order: Order,
    request: ReturnRequest,
    action: audit.Action,
    *,
    summary: str,
    before: ReturnStatus,
    extra: dict | None = None,
) -> None:
    audit.record(
        actor,
        action,
        target=order,
        summary=f"{order.number}: {summary}",
        before={"return_request": request.pk, "status": str(before)},
        after={"return_request": request.pk, "status": request.status, **(extra or {})},
    )


@transaction.atomic
def approve(actor: User, number: str, request_id: int) -> ReturnRequest:
    """Accept a request; the Shopper is told who pays the return shipping."""
    order = order_ops.locked(number)
    request = _locked_request(order, request_id)
    _expect(request, ReturnStatus.REQUESTED, step="approve")
    before = ReturnStatus(request.status)
    request.status = ReturnStatus.APPROVED
    request.decided_at = timezone.now()
    request.save(update_fields=["status", "decided_at"])
    shop_pays = request.reason in SHOP_PAYS_SHIPPING
    _step(
        actor,
        order,
        request,
        audit.Action.RETURN_APPROVED,
        summary=f"iade talebi #{request.pk} onaylandı",
        before=before,
        extra={"shop_pays_shipping": shop_pays},
    )
    outbox.notify_shopper(order, "return_approved", **_email_context(request))
    return request


@transaction.atomic
def reject(actor: User, number: str, request_id: int, *, reason: str) -> ReturnRequest:
    """Turn a request down. The reason goes to the Shopper; the quantities are freed."""
    order = order_ops.locked(number)
    request = _locked_request(order, request_id)
    _expect(request, ReturnStatus.REQUESTED, step="reject")
    reason = reason.strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise UnprocessableError(
            "Bir neden yazın.",
            fields={"reason": ["Bir neden yazın."]},
        )
    before = ReturnStatus(request.status)
    request.status = ReturnStatus.REJECTED
    request.decided_at = timezone.now()
    request.reject_reason = reason[:NOTE_MAX]
    request.save(update_fields=["status", "decided_at", "reject_reason"])
    _step(
        actor,
        order,
        request,
        audit.Action.RETURN_REJECTED,
        summary=f"iade talebi #{request.pk} reddedildi ({request.reject_reason})",
        before=before,
        extra={"reason": request.reject_reason},
    )
    outbox.notify_shopper(order, "return_rejected", **_email_context(request))
    return request


@transaction.atomic
def mark_received(
    actor: User,
    number: str,
    request_id: int,
    *,
    restock: dict[int, bool],
) -> ReturnRequest:
    """The parcel arrived. `restock` says, per Order line in the request, whether the
    units go back on the shelf (they may be damaged or worn)."""
    order = order_ops.locked(number)
    request = _locked_request(order, request_id)
    _expect(request, ReturnStatus.APPROVED, step="receive")
    rows = list(request.lines.select_related("order_line").order_by("pk"))
    if set(restock) != {row.order_line_id for row in rows}:
        msg = "Her iade satırı için stoğa dönüp dönmeyeceğini belirtin."
        raise UnprocessableError(msg, fields={"lines": [msg]})
    before = ReturnStatus(request.status)
    back = [row for row in rows if restock[row.order_line_id]]
    stock.restock(
        actor,
        ((row.order_line, row.quantity) for row in back),
        reason=f"iade talebi #{request.pk}, {order.number}",
    )
    for row in back:
        row.restocked = True
        row.save(update_fields=["restocked"])
    request.status = ReturnStatus.RECEIVED
    request.received_at = timezone.now()
    request.save(update_fields=["status", "received_at"])
    _step(
        actor,
        order,
        request,
        audit.Action.RETURN_RECEIVED,
        summary=f"iade talebi #{request.pk} teslim alındı",
        before=before,
        extra={"restocked": {str(r.order_line_id): r.quantity for r in back}},
    )
    outbox.notify_shopper(order, "return_received")
    return request


@transaction.atomic
def refund(  # noqa: PLR0913
    actor: User,
    number: str,
    request_id: int,
    *,
    amount: Decimal,
    method: RefundMethod,
    iban: str = "",
) -> ReturnRequest:
    """Record the money paid back for a received request (Owner only, as in phase 1).

    Several requests on one Order can each be refunded; together they can never
    exceed what the Shopper paid.
    """
    order = order_ops.locked(number)
    request = _locked_request(order, request_id)
    _expect(request, ReturnStatus.RECEIVED, step="refund")
    source = order_ops.source_of(order)
    refundable = source.amount_debited - source.amount_refunded
    if amount <= 0 or amount > refundable:
        msg = "İade tutarı ödenen tutarı aşamaz."
        raise UnprocessableError(msg, fields={"amount": [msg]})
    clean = order_ops.clean_iban(iban) if method == RefundMethod.BANK_TRANSFER else ""
    before = ReturnStatus(request.status)
    payment_before = tracking.payment_status_of(order)
    source.refund(amount)
    request.status = ReturnStatus.REFUNDED
    request.refund_amount = amount
    request.refund_method = method
    request.refund_iban = clean
    request.refunded_at = timezone.now()
    request.save(
        update_fields=[
            "status",
            "refund_amount",
            "refund_method",
            "refund_iban",
            "refunded_at",
        ],
    )
    audit.record(
        actor,
        audit.Action.REFUND,
        target=order,
        summary=f"{order.number}: iade talebi #{request.pk}, {amount} TL iade "
        f"({method.label})",
        before={
            "return_request": request.pk,
            "status": str(before),
            "payment_status": payment_before,
        },
        after={
            "return_request": request.pk,
            "status": request.status,
            "payment_status": tracking.payment_status_of(order),
            "amount": str(amount),
            "method": str(method),
        },
    )
    outbox.notify_shopper(
        order,
        "refund_issued",
        amount=pre_information.format_try(amount),
        method=method.label,
    )
    return request


def request_views(order: Order, *, staff: bool = False) -> list[dict]:
    """The Order's return requests as response dicts. The Shopper sees what they asked
    for and its status; Staff also see the decision, receipt and refund details."""
    requests = order.return_requests.prefetch_related("lines__order_line")
    out = []
    for r in requests:
        row: dict[str, Any] = {
            "id": r.pk,
            "status": r.status,
            "reason": r.reason,
            "note": r.note,
            "requested_at": r.requested_at,
            "lines": [
                {
                    "line_id": line.order_line_id,
                    "title": line.order_line.title,
                    "quantity": line.quantity,
                    **({"restocked": line.restocked} if staff else {}),
                }
                for line in sorted(r.lines.all(), key=lambda line: line.pk)
            ],
        }
        if staff:
            row |= {
                "shop_pays_shipping": r.reason in SHOP_PAYS_SHIPPING,
                "reject_reason": r.reject_reason,
                "decided_at": r.decided_at,
                "received_at": r.received_at,
                "refund_amount": r.refund_amount,
                "refund_method": r.refund_method,
                "refunded_at": r.refunded_at,
            }
        out.append(row)
    return out
