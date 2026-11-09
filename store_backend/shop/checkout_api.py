# ruff: noqa: TC001
"""Checkout: place an Order from the current basket. Guests and Customers."""

from __future__ import annotations

from http import HTTPStatus

from django.conf import settings
from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.client import client_ip
from store_backend.api.client import user_agent
from store_backend.api.errors import BadRequestError
from store_backend.api.money import Money
from store_backend.api.ratelimit import hit
from store_backend.services import checkout as checkout_service
from store_backend.services import shop_settings
from store_backend.shop.models import PaymentMethod

router = Router(tags=["checkout"], auth=GuestAuth())


class AddressIn(Schema):
    il: str = Field(max_length=100)
    ilce: str = Field(max_length=100)
    mahalle: str = Field(max_length=150)
    street: str = Field(max_length=200)
    postal_code: str = Field("", max_length=10)


class CheckoutIn(Schema):
    full_name: str = Field(max_length=150)
    email: str = Field(max_length=254)
    phone: str = Field(max_length=30)
    shipping_address: AddressIn
    billing_address: AddressIn | None = Field(
        None,
        description="Omit when billing is the same as shipping.",
    )
    payment_method: PaymentMethod
    contract_acknowledged: bool = Field(
        description="The unticked, mandatory distance-sales contract box.",
    )
    cod_fee_consent: bool = Field(
        default=False,
        description="Required for cash on delivery, even when the fee is zero.",
    )
    marketing_consent: bool = False
    website: str = Field(
        "",
        max_length=200,
        description="Honeypot: hidden from people, must stay empty.",
    )


class PlacedLineOut(Schema):
    sku: str
    title: str
    colour: str
    size: str
    quantity: int
    unit_price: Money
    line_total: Money


class BankOut(Schema):
    name: str
    account_holder: str
    iban: str
    reference: str = Field(description="Write this Order number in the transfer.")


class OrderPlacedOut(Schema):
    number: str
    status: str
    payment_status: str
    payment_method: PaymentMethod
    email: str
    lines: list[PlacedLineOut]
    items_total: Money
    shipping_fee: Money
    cod_fee: Money
    total: Money
    kdv_total: Money
    bank: BankOut | None


def _placed(order) -> dict:
    method = PaymentMethod(order.sources.get().source_type.code)
    surcharge = sum(
        (s.incl_tax for s in order.surcharges.all()),
        order.total_incl_tax * 0,
    )
    lines = [
        {
            "sku": line.partner_sku or line.upc,
            "title": line.product.parent.title,
            "colour": line.product.variant_profile.colour.name,
            "size": line.product.variant_profile.size,
            "quantity": line.quantity,
            "unit_price": line.unit_price_incl_tax,
            "line_total": line.line_price_incl_tax,
        }
        for line in order.lines.select_related(
            "product__parent",
            "product__variant_profile__colour",
        ).order_by("pk")
    ]
    bank = None
    if method == PaymentMethod.BANK_TRANSFER:
        store = shop_settings.get()
        bank = {
            "name": store.bank_name,
            "account_holder": store.bank_account_holder,
            "iban": store.bank_iban,
            "reference": order.number,
        }
    return {
        "number": order.number,
        "status": order.status,
        "payment_status": "unpaid",
        "payment_method": method,
        "email": order.guest_email,
        "lines": lines,
        "items_total": sum((line["line_total"] for line in lines), surcharge * 0),
        "shipping_fee": order.shipping_incl_tax,
        "cod_fee": surcharge,
        "total": order.total_incl_tax,
        "kdv_total": order.total_incl_tax - order.total_excl_tax,
        "bank": bank,
    }


@router.post(
    "/checkout",
    operation_id="checkout_place_order",
    response={HTTPStatus.CREATED: OrderPlacedOut},
)
def place_order(request, data: CheckoutIn):
    """Place the Order for the current basket.

    409 when stock changed since the basket was filled (the basket is left as is);
    422 with `fields` for form errors; 429 when this address tries too often.
    """
    limit, window = settings.CHECKOUT_RATE_LIMIT
    ip = client_ip(request)
    hit("checkout", ip, limit=limit, window=window)
    if data.website.strip():
        raise BadRequestError
    form = checkout_service.CheckoutForm(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        shipping_address=checkout_service.Address(**data.shipping_address.dict()),
        billing_address=(
            checkout_service.Address(**data.billing_address.dict())
            if data.billing_address
            else None
        ),
        payment_method=data.payment_method,
        contract_acknowledged=data.contract_acknowledged,
        cod_fee_consent=data.cod_fee_consent,
        marketing_consent=data.marketing_consent,
    )
    user = request.user if request.user.is_authenticated else None
    order = checkout_service.place_order(
        request.basket,
        form,
        checkout_service.Caller(
            user=user,
            ip_address=ip,
            user_agent=user_agent(request),
        ),
    )
    return HTTPStatus.CREATED, _placed(order)
