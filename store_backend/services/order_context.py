"""The facts every order email draws on, kept apart from the outbox that sends them.

Importing `tracking` and `payment` here is safe because neither imports checkout or the
outbox, so checkout can notify through the outbox without a cycle.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.utils import timezone

from store_backend.services import payment
from store_backend.services import pre_information
from store_backend.services import tracking
from store_backend.shop.models import PaymentMethod

if TYPE_CHECKING:
    from oscar.apps.order.models import Order

    from store_backend.shop.models import StoreSettings

PAYMENT_WINDOW = timedelta(hours=48)


def build(order: Order, store: StoreSettings) -> dict[str, Any]:
    """The facts every order email draws on. Links never carry phone or address."""
    lines = [
        {
            "title": line.product.parent.title,
            "colour": line.product.variant_profile.colour.name,
            "size": line.product.variant_profile.size,
            "quantity": line.quantity,
            "total": pre_information.format_try(line.line_price_incl_tax),
        }
        for line in sorted(order.lines.all(), key=lambda row: row.pk)
    ]
    base = settings.STOREFRONT_URL.rstrip("/")
    is_cod = payment.payment_method_of(order) == PaymentMethod.COD
    context = {
        "number": order.number,
        "is_cod": is_cod,
        # The bank details only make sense while the transfer is still due.
        "awaiting_payment": (
            not is_cod
            and order.status == "new"
            and tracking.payment_status_of(order) == "unpaid"
        ),
        "lines": lines,
        "total": pre_information.format_try(order.total_incl_tax),
        "shipping_fee": pre_information.format_try(order.shipping_incl_tax),
        "tracking_url": f"{base}/siparis-takip?t={tracking.link_token(order)}",
        "pay_by": timezone.localtime(order.date_placed + PAYMENT_WINDOW),
    }
    if is_cod:
        fee = sum((s.incl_tax for s in order.surcharges.all()), Decimal(0))
        context["cod_fee"] = pre_information.format_try(fee)
    return context
