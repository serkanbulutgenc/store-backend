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
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.services import checkout as checkout_service
from store_backend.services import contact
from store_backend.services import payment
from store_backend.services import pricing
from store_backend.services import shop_settings
from store_backend.services import tracking
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
    legal_versions: dict[str, str] | None = Field(
        None,
        description=(
            "Legal page versions the Shopper was shown (from GET /settings). A page "
            "that has changed since gives 409 `legal_changed`."
        ),
    )
    website: str = Field(
        "",
        max_length=200,
        description="Honeypot: hidden from people, must stay empty.",
    )


class QuoteIn(Schema):
    province: str = Field(
        "",
        max_length=100,
        description="Destination il; empty or unknown gives the flat Settings fee.",
    )
    payment_method: PaymentMethod


class QuoteOut(Schema):
    items_total: Money = Field(description="What the items cost before any Voucher.")
    discount: Money = Field(
        description="What the basket's Voucher takes off the items.",
    )
    voucher_code: str | None = Field(
        None,
        description="The Voucher the basket carries, if any.",
    )
    voucher_problem: str | None = Field(
        None,
        description="Why the Voucher is not applied (minimum); null when it is.",
    )
    shipping_fee: Money
    cod_fee: Money
    total: Money


def _basket_voucher_code(basket) -> str | None:
    voucher = basket.vouchers.first() if basket.id else None
    return voucher.code if voucher else None


def _address(data: AddressIn | None) -> contact.Address | None:
    return contact.Address(**data.dict()) if data else None


class PlacedLineOut(Schema):
    sku: str
    title: str
    colour: str
    size: str
    quantity: int
    unit_price: Money
    line_total: Money = Field(
        description="Before any Voucher, so lines add up to items_total.",
    )


class BankAccountOut(Schema):
    name: str
    account_holder: str
    iban: str


class BankOut(Schema):
    accounts: list[BankAccountOut] = Field(
        description="Any one of these will do, in the order the Owner set.",
    )
    reference: str = Field(description="Write this Order number in the transfer.")


class OrderPlacedOut(Schema):
    number: str
    status: str
    payment_status: str
    payment_method: PaymentMethod
    email: str
    lines: list[PlacedLineOut]
    items_total: Money = Field(description="What the items cost before any Voucher.")
    discount: Money = Field(description="What the Voucher took off the items.")
    voucher_code: str | None = Field(
        None,
        description="The Voucher this Order used, if any.",
    )
    shipping_fee: Money
    cod_fee: Money
    total: Money
    kdv_total: Money
    bank: BankOut | None
    tracking_token: str = Field(
        description="Signed link token (valid 30 days) for GET /tracking/link/{token}.",
    )


def placed_summary(order) -> dict:
    method = payment.payment_method_of(order)
    cod_fee = sum((s.incl_tax for s in order.surcharges.all()), pricing.ZERO)
    order_lines = list(
        order.lines.select_related(
            "product__parent",
            "product__variant_profile__colour",
        ).order_by("pk"),
    )
    lines = [
        {
            "id": line.pk,
            "sku": line.partner_sku or line.upc,
            "title": line.product.parent.title,
            "colour": line.product.variant_profile.colour.name,
            "size": line.product.variant_profile.size,
            "quantity": line.quantity,
            "unit_price": line.unit_price_incl_tax,
            "line_total": line.line_price_before_discounts_incl_tax,
        }
        for line in order_lines
    ]
    discounts = list(order.discounts.all())
    bank = None
    if method == PaymentMethod.BANK_TRANSFER:
        bank = {
            "accounts": shop_settings.bank_accounts(),
            "reference": order.number,
        }
    return {
        "number": order.number,
        "status": order.status,
        "payment_status": tracking.payment_status_of(order),
        "payment_method": method,
        "email": order.guest_email,
        "lines": lines,
        "items_total": sum(
            (line.line_price_before_discounts_incl_tax for line in order_lines),
            pricing.ZERO,
        ),
        "discount": sum((d.amount for d in discounts), pricing.ZERO),
        "voucher_code": next(
            (d.voucher_code for d in discounts if d.voucher_code),
            None,
        ),
        "shipping_fee": order.shipping_incl_tax,
        "cod_fee": cod_fee,
        "total": order.total_incl_tax,
        "kdv_total": order.total_incl_tax - order.total_excl_tax,
        "bank": bank,
        "tracking_token": tracking.link_token(order),
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
    ip = client_ip(request)
    count_attempt("checkout", ip, Rate(*settings.CHECKOUT_RATE_LIMIT))
    if data.website.strip():
        raise BadRequestError
    form = checkout_service.CheckoutForm(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        shipping_address=contact.Address(**data.shipping_address.dict()),
        billing_address=_address(data.billing_address),
        payment_method=data.payment_method,
        contract_acknowledged=data.contract_acknowledged,
        cod_fee_consent=data.cod_fee_consent,
        marketing_consent=data.marketing_consent,
        legal_versions=data.legal_versions,
        voucher_code=_basket_voucher_code(request.basket),
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
    return HTTPStatus.CREATED, placed_summary(order)


@router.post(
    "/checkout/quote",
    operation_id="checkout_quote",
    response=QuoteOut,
)
def quote_order(request, data: QuoteIn):
    """What the current basket would cost sent to `province`. Writes nothing.

    400 `basket_empty` when there is nothing to price.
    """
    quote = checkout_service.preview(
        request.basket,
        data.payment_method,
        data.province.strip() or None,
        request.user if request.user.is_authenticated else None,
    )
    cod = quote.fees.cod
    voucher = quote.voucher or quote.skipped_voucher
    return {
        "items_total": quote.items_total,
        "discount": quote.discount,
        "voucher_code": voucher.code if voucher else None,
        "voucher_problem": quote.voucher_problem,
        "shipping_fee": quote.fees.shipping.total_incl,
        "cod_fee": cod.total_incl if cod else pricing.ZERO,
        "total": quote.totals.total_incl,
    }
