"""Pricing a basket: lines, fees and totals. Reads settings and prices, no writes."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from store_backend.services import catalog
from store_backend.services import pricing
from store_backend.services import shipping
from store_backend.shop.models import PaymentMethod

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.basket.models import Line as BasketLine

    from store_backend.shop.models import StoreSettings


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
    totals: pricing.OrderTotals = field(init=False)

    def __post_init__(self) -> None:
        priced = [*self.snapshots.values(), self.fees.shipping]
        if self.fees.cod:
            priced.append(self.fees.cod)
        object.__setattr__(self, "totals", pricing.order_totals(priced))

    @property
    def items_total(self) -> Decimal:
        return sum((s.total_incl for s in self.snapshots.values()), pricing.ZERO)


def _snapshots(lines: list[BasketLine]) -> dict[int, pricing.LineSnapshot]:
    return {
        line.pk: pricing.line_snapshot(
            unit_price=catalog.shelf_price_for(line.product),
            quantity=line.quantity,
            kdv_rate=catalog.kdv_rate_for(line.product),
        )
        for line in lines
    }


def _shipping_amount(
    lines: list[BasketLine],
    items_total: Decimal,
    store: StoreSettings,
    province: str | None,
) -> Decimal:
    """Free above the threshold, else the cheapest carrier, else the flat fee."""
    threshold = store.free_shipping_threshold
    if threshold is not None and items_total >= threshold:
        return pricing.ZERO
    desi = sum(catalog.desi_for(line.product) * line.quantity for line in lines)
    computed = shipping.fee_for(desi, province)
    return computed.fee if computed else store.shipping_fee


def _fees(
    lines: list[BasketLine],
    items_total: Decimal,
    method: PaymentMethod,
    store: StoreSettings,
    province: str | None,
) -> Fees:
    rate = store.fee_kdv_rate
    shipping_fee = pricing.fee_line(
        _shipping_amount(lines, items_total, store, province),
        kdv_rate=rate,
    )
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
) -> Quote:
    """Price `lines` for a payment method under `store`'s fees. No writes.

    `province` is the destination il; without a known one (or desi) shipping is the
    flat Settings fee.
    """
    snapshots = _snapshots(lines)
    items_total = sum((s.total_incl for s in snapshots.values()), pricing.ZERO)
    return Quote(
        lines,
        snapshots,
        _fees(lines, items_total, method, store, province),
        store,
    )
