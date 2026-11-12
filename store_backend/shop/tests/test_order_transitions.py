"""The order lifecycle has one rule set: what `available_actions` offers is exactly
what the operations accept. Checked for every Fulfilment and Payment status pair."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.api.errors import ApiError
from store_backend.services import order_ops
from store_backend.services import tracking
from store_backend.shop.models import RefundMethod
from store_backend.shop.tests.test_orders import place

pytestmark = pytest.mark.django_db

STATUSES = (
    "new",
    "preparing",
    "shipped",
    "delivered",
    "completed",
    "cancelled",
    "return_requested",
    "returned",
)
PAYMENTS = (tracking.PAYMENT_UNPAID, tracking.PAYMENT_PAID, tracking.PAYMENT_REFUNDED)


def _operations(order, owner):
    number = order.number
    total = order.total_incl_tax
    street = order.shipping_address.line1
    return {
        "mark_paid": lambda: order_ops.mark_paid(
            owner,
            number,
            amount=total,
            paid_at=timezone.now() - timedelta(minutes=1),
        ),
        "cancel": lambda: order_ops.cancel(owner, number, reason="Müşteri istedi"),
        "edit_shipping": lambda: order_ops.edit_shipping(
            owner,
            number,
            street=street,
        ),
        "ship": lambda: order_ops.ship(
            owner,
            number,
            carrier="Yurtiçi",
            tracking_number="123",
        ),
        "deliver": lambda: order_ops.deliver(owner, number),
        "request_return": lambda: order_ops.request_return(owner, number),
        "mark_returned": lambda: order_ops.mark_returned(owner, number),
        "refund": lambda: order_ops.refund(
            owner,
            number,
            amount=Decimal("1.00"),
            method=RefundMethod.CASH,
        ),
    }


def _is_refused_as_illegal(call) -> bool:
    """Run the operation and undo it; True only for the illegal-transition conflict."""
    try:
        with transaction.atomic():
            call()
            transaction.set_rollback(True)
    except ApiError as error:
        return error.code == "illegal_transition"
    return False


def _put_in_state(number, status, payment):
    order = Order.objects.get(number=number)
    source = order.sources.get()
    paid = payment != tracking.PAYMENT_UNPAID
    refunded = payment == tracking.PAYMENT_REFUNDED
    source.amount_debited = order.total_incl_tax if paid else 0
    source.amount_refunded = Decimal("1.00") if refunded else 0
    source.save()
    Order.objects.filter(pk=order.pk).update(status=status)


@pytest.mark.parametrize("payment", PAYMENTS)
@pytest.mark.parametrize("status", STATUSES)
def test_available_actions_are_exactly_the_accepted_operations(
    shop,
    owner,
    status,
    payment,
):
    number = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    _put_in_state(number, status, payment)
    order = Order.objects.get(number=number)

    offered = set(order_ops.available_actions(order))
    accepted = {
        name
        for name, call in _operations(order, owner).items()
        if not _is_refused_as_illegal(call)
    }

    assert offered == accepted
