"""Pricing a basket: lines, fees and totals. Reads settings and prices, no writes."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from store_backend.services import basket_campaigns
from store_backend.services import campaign_pricing
from store_backend.services import catalog
from store_backend.services import pricing
from store_backend.services import shipping
from store_backend.shop.models import PaymentMethod

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.basket.models import Line as BasketLine
    from oscar.apps.voucher.models import Voucher

    from store_backend.shop.models import StoreSettings


class VoucherNotApplicableError(Exception):
    """The Voucher does not apply to this basket (for example below its minimum)."""


@dataclass(frozen=True)
class Fees:
    shipping: pricing.LineSnapshot
    cod: pricing.LineSnapshot | None


@dataclass(frozen=True)
class Quote:
    """What the basket costs: priced lines, fees and totals, all from one place.

    Carries the Store settings it was priced with, so placing the Order records the
    same legal versions and fees the Shopper was quoted.
    """

    lines: list[BasketLine]
    snapshots: dict[int, pricing.LineSnapshot]
    fees: Fees
    store: StoreSettings
    discount: Decimal = pricing.ZERO
    voucher: Voucher | None = None
    gross_snapshots: dict[int, pricing.LineSnapshot] = field(default_factory=dict)
    # How each line was priced, by basket line id: its Shelf price and winning Campaign.
    priced: dict[int, campaign_pricing.Priced] = field(default_factory=dict)
    skipped_voucher: Voucher | None = None  # on the basket, but not applied
    voucher_problem: str | None = None  # why it was not applied
    # The Basket Campaign this basket gets (ADR 0011), applied before any Voucher.
    campaign: basket_campaigns.Applied | None = None
    totals: pricing.OrderTotals = field(init=False)

    def __post_init__(self) -> None:
        priced = [*self.snapshots.values(), self.fees.shipping]
        if self.fees.cod:
            priced.append(self.fees.cod)
        object.__setattr__(self, "totals", pricing.order_totals(priced))

    @property
    def campaign_discount(self) -> Decimal:
        """What the Basket Campaign took off the items (0 if none, or free shipping)."""
        return self.campaign.discount if self.campaign else pricing.ZERO

    @property
    def items_total(self) -> Decimal:
        """What the items cost at Campaign prices, before any discount of the basket."""
        paid = sum((s.total_incl for s in self.snapshots.values()), pricing.ZERO)
        return paid + self.discount + self.campaign_discount


def _priced(lines: list[BasketLine]) -> dict[int, campaign_pricing.Priced]:
    """Each line at the price in force now: the Campaign price where one applies."""
    by_variant = campaign_pricing.priced(line.product for line in lines)
    return {line.pk: by_variant[line.product_id] for line in lines}


def _snapshots(
    lines: list[BasketLine],
    priced: dict[int, campaign_pricing.Priced],
) -> dict[int, pricing.LineSnapshot]:
    return {
        line.pk: pricing.line_snapshot(
            unit_price=priced[line.pk].unit,
            quantity=line.quantity,
            kdv_rate=catalog.kdv_rate_for(line.product),
        )
        for line in lines
    }


def _shipping_amount(
    lines: list[BasketLine],
    store: StoreSettings,
    province: str | None,
) -> Decimal:
    """The cheapest carrier's fee for the destination, else the flat Settings fee."""
    desi = sum(catalog.desi_for(line.product) * line.quantity for line in lines)
    computed = shipping.fee_for(desi, province)
    return computed.fee if computed else store.shipping_fee


def _fees(
    method: PaymentMethod,
    store: StoreSettings,
    shipping_amount: Decimal,
) -> Fees:
    rate = store.fee_kdv_rate
    shipping_fee = pricing.fee_line(shipping_amount, kdv_rate=rate)
    cod = (
        pricing.fee_line(store.cod_fee, kdv_rate=rate)
        if method == PaymentMethod.COD
        else None
    )
    return Fees(shipping=shipping_fee, cod=cod)


def quote(
    lines: list[BasketLine],
    method: PaymentMethod,
    store: StoreSettings,
    province: str | None = None,
    voucher: Voucher | None = None,
) -> Quote:
    """Price `lines` for a payment method under `store`'s fees. No writes.

    `province` is the destination il; without a known one (or desi) shipping is the
    flat Settings fee. The best Basket Campaign for the items applies first, then a
    `voucher` discounts the items only; fees are never discounted (a free-shipping
    Basket Campaign waives the shipping fee instead).
    """
    priced = _priced(lines)
    snapshots = gross = _snapshots(lines, priced)
    items_total = sum((s.total_incl for s in snapshots.values()), pricing.ZERO)
    shipping_amount = _shipping_amount(lines, store, province)
    # The Basket Campaign is decided on the items at Campaign prices, before a Voucher.
    campaign = basket_campaigns.best(items_total, shipping_amount)
    campaign_discount = campaign.discount if campaign else pricing.ZERO
    if campaign_discount:
        discounted = pricing.apply_discount(list(snapshots.values()), campaign_discount)
        snapshots = dict(zip(snapshots, discounted, strict=True))
    discount = pricing.ZERO
    if voucher is not None:
        offer = voucher.offers.get()
        if items_total < offer.condition.value:  # measured before both
            raise VoucherNotApplicableError
        benefit = offer.benefit
        # A percentage takes its share of what the Basket Campaign left.
        discount = pricing.voucher_discount(
            items_total - campaign_discount,
            benefit.type,
            benefit.value,
        )
        discounted = pricing.apply_discount(list(snapshots.values()), discount)
        snapshots = dict(zip(snapshots, discounted, strict=True))
    if campaign is not None and campaign.free_shipping:
        shipping_amount = pricing.ZERO
    return Quote(
        lines,
        snapshots,
        _fees(method, store, shipping_amount),
        store,
        discount,
        voucher,
        gross,
        priced=priced,
        campaign=campaign,
    )
