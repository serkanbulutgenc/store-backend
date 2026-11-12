"""How an Order is paid, as recorded on its payment Source."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oscar.apps.payment.models import Source
from oscar.apps.payment.models import SourceType

from store_backend.catalog.strategy import CURRENCY
from store_backend.shop.models import PaymentMethod

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.order.models import Order


def payment_method_of(order: Order) -> PaymentMethod:
    return PaymentMethod(order.sources.get().source_type.code)


def record_unpaid(order: Order, method: PaymentMethod, total: Decimal) -> None:
    """Nothing is debited yet: bank transfer waits for Staff, COD for delivery."""
    source_type, _ = SourceType.objects.get_or_create(
        code=method.value,
        defaults={"name": method.label},
    )
    Source.objects.create(
        order=order,
        source_type=source_type,
        currency=CURRENCY,
        amount_allocated=total,
    )
