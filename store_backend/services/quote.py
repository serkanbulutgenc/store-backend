"""Pricing a basket: lines, fees and totals. Reads settings and prices, no writes."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from store_backend.services import catalog
from store_backend.services import pricing
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


def _fees(items_total: Decimal, method: PaymentMethod, store: StoreSettings) -> Fees:
    rate = store.fee_kdv_rate
    threshold = store.free_shipping_threshold
    free = threshold is not None and items_total >= threshold
    shipping = pricing.fee_line(
        pricing.ZERO if free else store.shipping_fee,
        kdv_rate=rate,
    )
    cod = (
        pricing.fee_line(store.cod_fee, kdv_rate=rate)
        if method == PaymentMethod.COD
        else None
    )
    return Fees(shipping=shipping, cod=cod)


def quote(
    lines: list[BasketLine],
    method: PaymentMethod,
    store: StoreSettings,
) -> Quote:
    """Price `lines` for a payment method under `store`'s fees. No writes."""
    snapshots = _snapshots(lines)
    items_total = sum((s.total_incl for s in snapshots.values()), pricing.ZERO)
    return Quote(lines, snapshots, _fees(items_total, method, store), store)
