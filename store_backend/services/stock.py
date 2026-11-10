# ruff: noqa: EM101
"""Manual stock adjustments and the low-stock list."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from oscar.apps.catalogue.models import Product
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import outbox

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from store_backend.users.models import User

MIN_REASON_LENGTH = 3


def units(record: StockRecord | None) -> int:
    """Units free to sell: on hand minus what open Orders have reserved."""
    if record is None:
        return 0
    return max(record.num_in_stock - (record.num_allocated or 0), 0)


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
            on_hand=F("stockrecords__num_in_stock")
            - Coalesce(F("stockrecords__num_allocated"), 0),
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
