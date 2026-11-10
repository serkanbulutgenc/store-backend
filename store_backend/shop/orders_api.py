# ruff: noqa: TC001, TC003
"""Dashboard order operations: list, detail, transitions and edits."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from django.utils import timezone
from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.errors import UnprocessableError
from store_backend.api.money import Money
from store_backend.api.ordering import apply_ordering
from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import checkout
from store_backend.services import order_ops
from store_backend.services import pricing
from store_backend.services import tracking
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import RefundMethod
from store_backend.staff.models import AuditEntry
from store_backend.staff.permissions import Perm

router = Router(tags=["orders"])

ORDERING_FIELDS = ("date_placed", "total_incl_tax", "number")
PaymentStatus = Literal["unpaid", "paid", "refunded"]


# --- output -------------------------------------------------------------------


class OrderRow(Schema):
    number: str
    placed_at: datetime
    status: str
    payment_status: PaymentStatus
    customer: str
    email: str
    total: Money
    item_count: int

    @staticmethod
    def resolve_placed_at(obj) -> datetime:
        return obj.date_placed

    @staticmethod
    def resolve_payment_status(obj) -> str:
        return tracking.payment_status_of(obj)

    @staticmethod
    def resolve_customer(obj) -> str:
        address = obj.shipping_address
        return f"{address.first_name} {address.last_name}".strip() if address else ""

    @staticmethod
    def resolve_email(obj) -> str:
        return obj.guest_email or (obj.user.email if obj.user else "")

    @staticmethod
    def resolve_total(obj):
        return obj.total_incl_tax

    @staticmethod
    def resolve_item_count(obj) -> int:
        return sum(line.quantity for line in obj.lines.all())


class OrderLineOut(Schema):
    sku: str
    title: str
    colour: str
    size: str
    quantity: int
    unit_price: Money
    line_total: Money


class OrderShippingOut(Schema):
    full_name: str
    phone: str
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str


class OrderAuditOut(Schema):
    at: datetime
    actor: str
    action: str
    summary: str


class OrderDetail(Schema):
    number: str
    placed_at: datetime
    status: str
    payment_status: PaymentStatus
    payment_method: PaymentMethod
    email: str
    customer_id: int | None
    shipping_address: OrderShippingOut | None
    lines: list[OrderLineOut]
    items_total: Money
    shipping_fee: Money
    cod_fee: Money
    total: Money
    kdv_total: Money
    actions: list[str]
    paid_amount: Money | None
    paid_at: datetime | None
    cancel_reason: str
    carrier: str
    tracking_number: str
    shipped_at: datetime | None
    delivered_at: datetime | None
    refund_amount: Money | None
    refund_method: str
    refund_iban: str
    refunded_at: datetime | None
    buyer_tax_number: str
    company_title: str
    audit: list[OrderAuditOut]


def _shipping(order) -> dict | None:
    address = order.shipping_address
    if address is None:
        return None
    return {
        "full_name": f"{address.first_name} {address.last_name}".strip(),
        "phone": getattr(address.phone_number, "as_e164", "") or "",
        "il": address.state,
        "ilce": address.line4,
        "mahalle": address.line3,
        "street": address.line1,
        "postal_code": address.postcode,
    }


def _lines(order) -> list[dict]:
    return [
        {
            "sku": line.partner_sku or line.upc,
            "title": line.product.parent.title,
            "colour": line.product.variant_profile.colour.name,
            "size": line.product.variant_profile.size,
            "quantity": line.quantity,
            "unit_price": line.unit_price_incl_tax,
            "line_total": line.line_price_incl_tax,
        }
        for line in sorted(order.lines.all(), key=lambda row: row.pk)
    ]


def _audit(order) -> list[dict]:
    entries = AuditEntry.objects.filter(
        target_type=order._meta.label_lower,  # noqa: SLF001
        target_id=str(order.pk),
    ).order_by("created_at", "id")
    return [
        {
            "at": e.created_at,
            "actor": e.actor_email or "Sistem",
            "action": e.action,
            "summary": e.summary,
        }
        for e in entries
    ]


def order_detail(order) -> dict:
    record = order_ops.peek_record(order)
    lines = _lines(order)
    cod_fee = sum((s.incl_tax for s in order.surcharges.all()), pricing.ZERO)
    return {
        "number": order.number,
        "placed_at": order.date_placed,
        "status": order.status,
        "payment_status": tracking.payment_status_of(order),
        "payment_method": checkout.payment_method_of(order),
        "email": order.guest_email or (order.user.email if order.user else ""),
        "customer_id": order.user_id,
        "shipping_address": _shipping(order),
        "lines": lines,
        "items_total": sum((line["line_total"] for line in lines), pricing.ZERO),
        "shipping_fee": order.shipping_incl_tax,
        "cod_fee": cod_fee,
        "total": order.total_incl_tax,
        "kdv_total": order.total_incl_tax - order.total_excl_tax,
        "actions": order_ops.available_actions(order),
        "paid_amount": record.paid_amount,
        "paid_at": record.paid_at,
        "cancel_reason": record.cancel_reason,
        "carrier": record.carrier,
        "tracking_number": record.tracking_number,
        "shipped_at": record.shipped_at,
        "delivered_at": record.delivered_at,
        "refund_amount": record.refund_amount,
        "refund_method": record.refund_method,
        "refund_iban": record.refund_iban,
        "refunded_at": record.refunded_at,
        "buyer_tax_number": record.buyer_tax_number,
        "company_title": record.company_title,
        "audit": _audit(order),
    }


def _fresh(order) -> dict:
    return order_detail(order_ops.get(order.number))


# --- input --------------------------------------------------------------------

Amount = Field(gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)


class OrderPayIn(Schema):
    amount: Decimal = Amount
    paid_at: datetime | None = Field(None, description="Defaults to now.")


class OrderCancelIn(Schema):
    reason: str = Field(max_length=500)


class OrderShipIn(Schema):
    carrier: str = Field(max_length=100)
    tracking_number: str = Field(max_length=100)


class OrderRefundIn(Schema):
    amount: Decimal = Amount
    method: RefundMethod
    iban: str = Field("", max_length=50, description="Required for bank transfer.")


class OrderShippingPatch(Schema):
    """Only the fields sent change."""

    phone: str | None = Field(None, max_length=30)
    il: str | None = Field(None, max_length=100)
    ilce: str | None = Field(None, max_length=100)
    mahalle: str | None = Field(None, max_length=150)
    street: str | None = Field(None, max_length=200)
    postal_code: str | None = Field(None, max_length=10)


class OrderEmailIn(Schema):
    email: str = Field(max_length=254)


class OrderInvoiceDetailsPatch(Schema):
    buyer_tax_number: str | None = Field(None, max_length=50)
    company_title: str | None = Field(None, max_length=200)


# --- endpoints ----------------------------------------------------------------


@router.get(
    "/orders",
    auth=requires(Perm.VIEW_ORDERS),
    operation_id="orders_list",
    response=list[OrderRow],
)
@paginate(PageOffsetPagination)
def list_orders(
    request,
    q: str | None = None,
    status: str | None = None,
    payment_status: str | None = None,
    ordering: str | None = None,
):
    """Newest first. `q` matches number, email and name. `payment_status` is one of
    unpaid, paid, refunded."""
    if payment_status and payment_status not in order_ops.PAYMENT_STATES:
        msg = "Geçersiz ödeme durumu."
        raise UnprocessableError(msg, fields={"payment_status": [msg]})
    orders = order_ops.search(
        q=q,
        status=status,
        payment_status=payment_status,
    )
    return apply_ordering(orders, ordering, allowed=ORDERING_FIELDS)


@router.get(
    "/orders/{number}",
    auth=requires(Perm.VIEW_ORDERS),
    operation_id="orders_get",
    response=OrderDetail,
)
def get_order(request, number: str):
    return order_detail(order_ops.get(number))


@router.post(
    "/orders/{number}/pay",
    auth=requires(Perm.MARK_PAID),
    operation_id="orders_mark_paid",
    response=OrderDetail,
)
def mark_paid(request, number: str, data: OrderPayIn):
    """Record the payment. 409 when already paid or cancelled."""
    order = order_ops.mark_paid(
        request.auth,
        number,
        amount=data.amount,
        paid_at=data.paid_at or timezone.now(),
    )
    return _fresh(order)


@router.post(
    "/orders/{number}/cancel",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_cancel",
    response=OrderDetail,
)
def cancel(request, number: str, data: OrderCancelIn):
    """Cancel before shipping, releasing stock. A paid Order is Owner-only (403)."""
    return _fresh(order_ops.cancel(request.auth, number, reason=data.reason))


@router.post(
    "/orders/{number}/ship",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_ship",
    response=OrderDetail,
)
def ship(request, number: str, data: OrderShipIn):
    """Only a preparing Order ships. Consumes the reserved stock."""
    order = order_ops.ship(
        request.auth,
        number,
        carrier=data.carrier,
        tracking_number=data.tracking_number,
    )
    return _fresh(order)


@router.post(
    "/orders/{number}/deliver",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_deliver",
    response=OrderDetail,
)
def deliver(request, number: str):
    """Cash on delivery becomes paid here."""
    return _fresh(order_ops.deliver(request.auth, number))


@router.post(
    "/orders/{number}/request-return",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_request_return",
    response=OrderDetail,
)
def request_return(request, number: str):
    return _fresh(order_ops.request_return(request.auth, number))


@router.post(
    "/orders/{number}/mark-returned",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_mark_returned",
    response=OrderDetail,
)
def mark_returned(request, number: str):
    return _fresh(order_ops.mark_returned(request.auth, number))


@router.post(
    "/orders/{number}/refund",
    auth=requires(Perm.RECORD_REFUNDS),
    operation_id="orders_refund",
    response=OrderDetail,
)
def refund(request, number: str, data: OrderRefundIn):
    """Owner-only. For a paid Order that was returned or cancelled."""
    order = order_ops.refund(
        request.auth,
        number,
        amount=data.amount,
        method=data.method,
        iban=data.iban,
    )
    return _fresh(order)


@router.patch(
    "/orders/{number}/shipping",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_edit_shipping",
    response=OrderDetail,
)
def edit_shipping(request, number: str, data: OrderShippingPatch):
    """Address and phone, only while the Order is new or preparing (409 after)."""
    changes = {
        k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None
    }
    return _fresh(order_ops.edit_shipping(request.auth, number, **changes))


@router.patch(
    "/orders/{number}/email",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_correct_email",
    response=OrderDetail,
)
def correct_email(request, number: str, data: OrderEmailIn):
    return _fresh(order_ops.correct_email(request.auth, number, email=data.email))


@router.patch(
    "/orders/{number}/invoice-details",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_set_invoice_details",
    response=OrderDetail,
)
def set_invoice_details(request, number: str, data: OrderInvoiceDetailsPatch):
    changes = {
        k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None
    }
    return _fresh(order_ops.set_invoice_details(request.auth, number, **changes))
