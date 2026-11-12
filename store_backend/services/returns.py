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

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import order_ops
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnReason
from store_backend.shop.models import ReturnRequest
from store_backend.shop.models import ReturnStatus

if TYPE_CHECKING:
    from datetime import datetime

    from oscar.apps.order.models import Line
    from oscar.apps.order.models import Order

RETURN_WINDOW = timedelta(days=14)
RETURNABLE_STATUSES = (order_ops.STATUS_DELIVERED, order_ops.STATUS_COMPLETED)
NOTE_MAX = 500


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
    return request
