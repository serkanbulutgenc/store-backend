"""Writing an Order through Oscar. The only place checkout touches Oscar's order models.

`create_order` stores the addresses, the Order with its lines and KDV snapshots, the
fee snapshots and the unpaid payment Source, all inside the caller's transaction.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from oscar.apps.address.models import Country
from oscar.apps.order.models import BillingAddress
from oscar.apps.order.models import Order
from oscar.apps.order.models import ShippingAddress
from oscar.apps.order.models import Surcharge
from oscar.apps.order.utils import OrderCreator
from oscar.core.prices import Price

from store_backend.catalog.strategy import CURRENCY
from store_backend.services import payment
from store_backend.services import pricing
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import OrderLineSnapshot

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.basket.models import Basket

    from store_backend.services.contact import Address
    from store_backend.services.quote import Fees
    from store_backend.services.quote import Quote
    from store_backend.shop.models import PaymentMethod
    from store_backend.users.models import User

SHIPPING_CODE = "flat"
SHIPPING_NAME = "Standart kargo"

# No vowels or look-alikes (0/O, 1/I): easy to read out and to type into a transfer.
NUMBER_ALPHABET = "23456789BCDFGHJKLMNPQRSTVWXYZ"
NUMBER_LENGTH = 6
NUMBER_PREFIX = "AT-"
NUMBER_ATTEMPTS = 5


class _ShippingMethod:
    """The shipping fee as Oscar's `OrderCreator` wants to see a shipping method."""

    name = SHIPPING_NAME
    code = SHIPPING_CODE

    def discount(self, basket: Basket) -> Decimal:
        return pricing.ZERO


class _SnapshotOrderCreator(OrderCreator):
    """Builds Order lines from the KDV snapshots, so stored totals equal their sum.

    Oscar's strategy prices are per unit; splitting each line total once (see
    `pricing`) is what keeps stored line amounts, snapshots and the Order total equal.
    """

    def __init__(self, snapshots: dict[int, pricing.LineSnapshot]) -> None:
        self.snapshots = snapshots

    def create_additional_line_models(self, order, order_line, basket_line):
        snap = self.snapshots[basket_line.pk]
        order_line.line_price_incl_tax = snap.total_incl
        order_line.line_price_excl_tax = snap.total_excl
        order_line.line_price_before_discounts_incl_tax = snap.total_incl
        order_line.line_price_before_discounts_excl_tax = snap.total_excl
        order_line.unit_price_incl_tax = snap.unit_price_incl
        order_line.unit_price_excl_tax = snap.unit_price_excl
        order_line.save()
        OrderLineSnapshot.objects.create(
            line=order_line,
            quantity=snap.quantity,
            unit_price_incl=snap.unit_price_incl,
            kdv_rate=snap.kdv_rate,
            total_incl=snap.total_incl,
            total_excl=snap.total_excl,
            total_kdv=snap.total_kdv,
        )


def _order_number() -> str:
    while True:
        code = "".join(secrets.choice(NUMBER_ALPHABET) for _ in range(NUMBER_LENGTH))
        number = NUMBER_PREFIX + code
        if not Order.objects.filter(number=number).exists():
            return number


def _address_fields(full_name: str, address: Address) -> dict:
    first, _, last = full_name.rpartition(" ")
    country, _ = Country.objects.get_or_create(
        iso_3166_1_a2="TR",
        defaults={
            "iso_3166_1_a3": "TUR",
            "iso_3166_1_numeric": "792",
            "printable_name": "Türkiye",
            "name": "Türkiye",
            "is_shipping_country": True,
        },
    )
    return {
        "first_name": first,
        "last_name": last,
        "line1": address.street,
        "line3": address.mahalle,
        "line4": address.ilce,
        "state": address.il,
        "postcode": address.postal_code,
        "country": country,
    }


def _write_fee_snapshots(order: Order, fees: Fees) -> None:
    for kind, fee in (
        (OrderFeeSnapshot.Kind.SHIPPING, fees.shipping),
        (OrderFeeSnapshot.Kind.COD, fees.cod),
    ):
        if fee is None or fee.total_incl == 0:
            continue
        OrderFeeSnapshot.objects.create(
            order=order,
            kind=kind,
            kdv_rate=fee.kdv_rate,
            total_incl=fee.total_incl,
            total_excl=fee.total_excl,
            total_kdv=fee.total_kdv,
        )
        if kind == OrderFeeSnapshot.Kind.COD:
            Surcharge.objects.create(
                order=order,
                name=kind.label,
                code=kind.value,
                incl_tax=fee.total_incl,
                excl_tax=fee.total_excl,
            )


def _with_unique_number(creator: OrderCreator, **kwargs) -> Order:
    """Place the Order under a fresh number, retrying if two checkouts drew the same."""
    for attempt in range(NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                return creator.place_order(order_number=_order_number(), **kwargs)
        except IntegrityError, ValueError:
            if attempt == NUMBER_ATTEMPTS - 1:
                raise
    raise AssertionError  # unreachable


def create_order(  # noqa: PLR0913
    *,
    basket: Basket,
    quote: Quote,
    method: PaymentMethod,
    status: str,
    user: User | None,
    email: str,
    full_name: str,
    phone: str,
    shipping: Address,
    billing: Address,
) -> Order:
    totals, fees = quote.totals, quote.fees
    shipping_address = ShippingAddress.objects.create(
        phone_number=phone,
        **_address_fields(full_name, shipping),
    )
    billing_address = BillingAddress.objects.create(
        **_address_fields(full_name, billing),
    )
    order = _with_unique_number(
        _SnapshotOrderCreator(quote.snapshots),
        basket=basket,
        total=Price(CURRENCY, excl_tax=totals.total_excl, tax=totals.total_kdv),
        shipping_method=_ShippingMethod(),
        shipping_charge=Price(
            CURRENCY,
            excl_tax=fees.shipping.total_excl,
            tax=fees.shipping.total_kdv,
        ),
        user=user,
        shipping_address=shipping_address,
        billing_address=billing_address,
        status=status,
        guest_email=email,
    )
    _write_fee_snapshots(order, fees)
    payment.record_unpaid(order, method, totals.total_incl)
    return order
