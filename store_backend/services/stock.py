# ruff: noqa: EM101, TRY003
"""Stock: what is free to sell, which record counts, reservations and adjustments.

A Variant's stock is its first `StockRecord` by id (the record Oscar's strategy
uses). Free to sell is on hand minus what open Orders have reserved (`num_allocated`).
Placing an Order reserves units; cancelling releases them; shipping consumes them.
Whenever rows are locked, they are locked in primary-key order, so two Orders that
share Variants cannot deadlock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from oscar.apps.catalogue.models import Product
from oscar.apps.partner.exceptions import InvalidStockAdjustment
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import outbox

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import Expression
    from django.db.models import QuerySet
    from oscar.apps.order.models import Order

    from store_backend.users.models import User

MIN_REASON_LENGTH = 3


def units(record: StockRecord | None) -> int:
    """Units free to sell: on hand minus what open Orders have reserved."""
    if record is None:
        return 0
    return max(record.num_in_stock - (record.num_allocated or 0), 0)


def available_sql(prefix: str = "") -> Expression:
    """`units` as a SQL expression; `prefix` is the path to the stock record columns
    (`"stockrecords__"` from a Variant, `"product__stockrecords__"` from its profile).
    Unlike `units` it can go negative; filter on it with `> 0` or `<= threshold`."""
    return F(f"{prefix}num_in_stock") - Coalesce(F(f"{prefix}num_allocated"), 0)


def is_low(record: StockRecord | None, threshold: int) -> bool:
    """At or below the low-stock threshold, counting reserved units as gone."""
    return (record.num_in_stock - (record.num_allocated or 0) if record else 0) <= (
        threshold
    )


def record_for(variant: Product) -> StockRecord | None:
    """The stock record that counts for this Variant, or None."""
    return StockRecord.objects.filter(product=variant).order_by("pk").first()


def records_for(
    variant_ids: Iterable[int],
    *,
    lock: bool = False,
) -> dict[int, StockRecord]:
    """The stock record that counts for each Variant id. `lock=True` row-locks them
    (in primary-key order) for the rest of the transaction."""
    records = StockRecord.objects.filter(product_id__in=list(variant_ids))
    if lock:
        records = records.select_for_update()
    found: dict[int, StockRecord] = {}
    for record in records.order_by("pk"):
        found.setdefault(record.product_id, record)  # lowest pk wins
    return found


def _reserved(order: Order) -> list[tuple[StockRecord, int]]:
    """(stock record, quantity) for each line of the Order, locked in pk order."""
    quantities: dict[int, int] = {}
    for line in order.lines.all():
        if line.stockrecord_id is not None:
            quantities[line.stockrecord_id] = (
                quantities.get(line.stockrecord_id, 0) + line.quantity
            )
    records = StockRecord.objects.select_for_update().filter(pk__in=quantities)
    return [(r, quantities[r.pk]) for r in records.order_by("pk")]


def release(order: Order) -> None:
    """Give an Order's reserved units back (it was cancelled)."""
    for record, quantity in _reserved(order):
        record.cancel_allocation(quantity)


def consume(order: Order) -> None:
    """Take an Order's units off the shelf and off the reservation (it shipped)."""
    for record, quantity in _reserved(order):
        try:
            record.consume_allocation(quantity)
        except InvalidStockAdjustment:
            raise ConflictError(
                "Stok kaydı bu siparişi göndermek için yetersiz. Stoğu kontrol edin.",
                code="stock_mismatch",
            ) from None


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


@transaction.atomic
def adjust(actor: User, variant: Product, *, delta: int, reason: str) -> StockRecord:
    """Add `delta` (negative to remove) to a Variant's on-hand stock, with a reason.

    On-hand stock cannot go below zero. Units reserved by open Orders are not
    subtracted: staff count the shelf, and reservations sit on top of that count.
    """
    reason = reason.strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise _invalid("reason", "Bir neden yazın.")
    if delta == 0:
        raise _invalid("delta", "Değişiklik miktarı sıfır olamaz.")
    record = StockRecord.objects.select_for_update().get(product=variant)
    new_level = record.num_in_stock + delta
    if new_level < 0:
        raise _invalid("delta", "Stok sıfırın altına düşemez.")
    before = record.num_in_stock
    record.num_in_stock = new_level
    record.save(update_fields=["num_in_stock"])
    audit.record(
        actor,
        audit.Action.STOCK_ADJUSTMENT,
        target=variant,
        summary=f"{variant.variant_profile.sku}: {delta:+d} ({reason})",
        before={"num_in_stock": before},
        after={"num_in_stock": new_level, "reason": reason},
    )
    return record


def low_stock_variants() -> QuerySet[Product]:
    """Variants of non-archived Products at or below their Product's threshold."""
    return (
        Product.objects.filter(structure=Product.CHILD)
        .exclude(parent__profile__status="archived")
        .annotate(
            on_hand=available_sql("stockrecords__"),
        )
        .filter(on_hand__lte=F("parent__profile__low_stock_threshold"))
        .select_related("variant_profile__colour", "parent__profile")
        .order_by("on_hand", "variant_profile__sku")
    )


def send_low_stock_digest() -> int:
    """Email the configured recipients one list of low variants. Returns the count."""
    rows = [
        {
            "title": v.parent.title,
            "colour": v.variant_profile.colour.name,
            "size": v.variant_profile.size,
            "sku": v.variant_profile.sku,
            "on_hand": v.on_hand,
        }
        for v in low_stock_variants()
    ]
    if rows:
        outbox.notify_staff("low_stock_digest", {"rows": rows, "count": len(rows)})
    return len(rows)
