# ruff: noqa: TC001, TC003
"""Dashboard order operations: list, detail, transitions and edits."""

from __future__ import annotations

import datetime as dt
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
from store_backend.services import invoicing
from store_backend.services import order_ops
from store_backend.services import payment
from store_backend.services import pricing
from store_backend.services import returns
from store_backend.services import tracking
from store_backend.shop.models import InvoiceStatus
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import RefundMethod
from store_backend.shop.models import ReturnReason
from store_backend.shop.models import ReturnStatus
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


class OrderMessageOut(Schema):
    id: int
    recipient: str
    template: str
    subject: str
    created_at: datetime
    sent_at: datetime | None
    status: Literal["pending", "sent", "failed", "bounced"]
    attempts: int
    error: str
    body_text: str
    body_html: str


class InvoiceOut(Schema):
    status: InvoiceStatus
    number: str
    date: dt.date | None
    cancel_by: dt.date | None = Field(
        description="Last day the portal lets Staff cancel the invoice (8 days).",
    )
    credit_note_number: str
    credit_note_date: dt.date | None


class ReturnLineAdminOut(Schema):
    line_id: int
    title: str
    quantity: int
    restocked: bool


class ReturnAdminOut(Schema):
    id: int
    status: ReturnStatus
    reason: ReturnReason
    note: str
    requested_at: datetime
    shop_pays_shipping: bool
    reject_reason: str
    decided_at: datetime | None
    received_at: datetime | None
    refund_amount: Money | None
    refund_method: str
    refunded_at: datetime | None
    lines: list[ReturnLineAdminOut]


class ReturnRow(Schema):
    id: int
    number: str
    customer: str
    status: ReturnStatus
    reason: ReturnReason
    requested_at: datetime
    item_count: int

    @staticmethod
    def resolve_number(obj) -> str:
        return obj.order.number

    @staticmethod
    def resolve_customer(obj) -> str:
        address = obj.order.shipping_address
        return f"{address.first_name} {address.last_name}".strip() if address else ""

    @staticmethod
    def resolve_item_count(obj) -> int:
        return sum(line.quantity for line in obj.lines.all())


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
    messages: list[OrderMessageOut]
    invoice: InvoiceOut
    returns: list[ReturnAdminOut]


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


def _messages(order) -> list[dict]:
    return [
        {
            "id": m.pk,
            "recipient": m.recipient,
            "template": m.template,
            "subject": m.subject,
            "created_at": m.created_at,
            "sent_at": m.sent_at,
            "status": m.status,
            "attempts": m.attempts,
            "error": m.bounce_reason if m.status == "bounced" else m.last_error,
            "body_text": m.body_text,
            "body_html": m.body_html,
        }
        for m in order.messages.all()
    ]


def _invoice(order) -> dict:
    invoice = invoicing.peek(order)
    return {
        "status": invoice.status,
        "number": invoice.number,
        "date": invoice.date,
        "cancel_by": invoicing.cancel_by(invoice),
        "credit_note_number": invoice.credit_note_number,
        "credit_note_date": invoice.credit_note_date,
    }


def _returns(order) -> list[dict]:
    requests = order.return_requests.prefetch_related("lines__order_line")
    return [
        {
            "id": r.pk,
            "status": r.status,
            "reason": r.reason,
            "note": r.note,
            "requested_at": r.requested_at,
            "shop_pays_shipping": r.reason in returns.SHOP_PAYS_SHIPPING,
            "reject_reason": r.reject_reason,
            "decided_at": r.decided_at,
            "received_at": r.received_at,
            "refund_amount": r.refund_amount,
            "refund_method": r.refund_method,
            "refunded_at": r.refunded_at,
            "lines": [
                {
                    "line_id": row.order_line_id,
                    "title": row.order_line.title,
                    "quantity": row.quantity,
                    "restocked": row.restocked,
                }
                for row in sorted(r.lines.all(), key=lambda row: row.pk)
            ],
        }
        for r in requests
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
        "payment_method": payment.payment_method_of(order),
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
        "messages": _messages(order),
        "invoice": _invoice(order),
        "returns": _returns(order),
    }


def _fresh(order) -> dict:
    return order_detail(order_ops.get(order.number))


def _fresh_number(number: str) -> dict:
    return order_detail(order_ops.get(number))


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


class ReturnRejectIn(Schema):
    reason: str = Field(max_length=500, description="Shown to the Shopper.")


class ReturnReceiveLineIn(Schema):
    line_id: int
    restock: bool = Field(description="Put the units back on the shelf.")


class ReturnReceiveIn(Schema):
    lines: list[ReturnReceiveLineIn] = Field(
        description="One entry for every line of the request.",
    )


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


class InvoiceIn(Schema):
    """The whole record, as it stands in the GIB portal."""

    status: InvoiceStatus
    number: str = Field("", max_length=50, description="Invoice number.")
    date: dt.date | None = None
    credit_note_number: str = Field("", max_length=50)
    credit_note_date: dt.date | None = None


class SheetBuyer(Schema):
    name: str
    tax_number: str
    company_title: str
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str


class SheetLine(Schema):
    kind: Literal["product", "shipping", "cod"]
    description: str
    quantity: int
    kdv_rate: Money
    unit_price_excl: Money
    total_excl: Money
    total_kdv: Money
    total_incl: Money


class InvoiceSheet(Schema):
    order_number: str
    order_date: datetime
    buyer: SheetBuyer
    lines: list[SheetLine]
    total_excl: Money
    total_kdv: Money
    total_incl: Money


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
    "/returns",
    auth=requires(Perm.VIEW_ORDERS),
    operation_id="returns_list",
    response=list[ReturnRow],
)
@paginate(PageOffsetPagination)
def list_returns(request, status: str | None = None):
    """Return requests across all Orders. Waiting requests come oldest first, any
    other filter (or none) newest first."""
    return returns.search(status=status)


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


@router.post(
    "/orders/{number}/returns/{return_id}/approve",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_return_approve",
    response=OrderDetail,
)
def approve_return(request, number: str, return_id: int):
    """Accept a return request. The Shopper's email says who pays return shipping."""
    returns.approve(request.auth, number, return_id)
    return _fresh_number(number)


@router.post(
    "/orders/{number}/returns/{return_id}/reject",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_return_reject",
    response=OrderDetail,
)
def reject_return(request, number: str, return_id: int, data: ReturnRejectIn):
    """Turn a return request down with a reason the Shopper will read."""
    returns.reject(request.auth, number, return_id, reason=data.reason)
    return _fresh_number(number)


@router.post(
    "/orders/{number}/returns/{return_id}/receive",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_return_receive",
    response=OrderDetail,
)
def receive_return(request, number: str, return_id: int, data: ReturnReceiveIn):
    """The parcel arrived; per line, say whether the units go back on the shelf."""
    returns.mark_received(
        request.auth,
        number,
        return_id,
        restock={row.line_id: row.restock for row in data.lines},
    )
    return _fresh_number(number)


@router.post(
    "/orders/{number}/returns/{return_id}/refund",
    auth=requires(Perm.RECORD_REFUNDS),
    operation_id="orders_return_refund",
    response=OrderDetail,
)
def refund_return(request, number: str, return_id: int, data: OrderRefundIn):
    """Owner-only. Record the money paid back for a received return request."""
    returns.refund(
        request.auth,
        number,
        return_id,
        amount=data.amount,
        method=data.method,
        iban=data.iban,
    )
    return _fresh_number(number)


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


@router.post(
    "/orders/{number}/messages/{message_id}/resend",
    auth=requires(Perm.MANAGE_ORDERS),
    operation_id="orders_resend_message",
    response=OrderDetail,
)
def resend_message(request, number: str, message_id: int):
    """Queue a fresh copy of a logged email; the original stays in the log."""
    return _fresh(order_ops.resend_message(request.auth, number, message_id))


@router.put(
    "/orders/{number}/invoice",
    auth=requires(Perm.MANAGE_INVOICES),
    operation_id="orders_set_invoice",
    response=OrderDetail,
)
def set_invoice(request, number: str, data: InvoiceIn):
    """Record the invoice Staff issued by hand in the GIB portal. Replaces the whole
    record; a number already used by another Order is 409."""
    order = invoicing.record(
        request.auth,
        number,
        status=data.status,
        invoice_number=data.number,
        invoice_date=data.date,
        credit_note_number=data.credit_note_number,
        credit_note_date=data.credit_note_date,
    )
    return _fresh(order)


@router.get(
    "/orders/{number}/invoice-sheet",
    auth=requires(Perm.MANAGE_INVOICES),
    operation_id="orders_invoice_sheet",
    response=InvoiceSheet,
)
def invoice_sheet(request, number: str):
    """Buyer, lines and KDV split laid out for typing into the portal."""
    return invoicing.sheet(order_ops.get(number))
