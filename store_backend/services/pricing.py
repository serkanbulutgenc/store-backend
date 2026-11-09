"""KDV arithmetic: splitting a KDV-inclusive amount, line snapshots and order totals.

All shelf prices are KDV-inclusive kuruş amounts. The exclusive amount is rounded
half-up to kuruş and KDV is the remainder, so `excl + kdv == incl` always holds. Each
line is split once on its own total (never unit-by-unit), and an order total is the
plain sum of its rounded lines, so totals can never drift from the lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP
from decimal import Decimal

from django.conf import settings

KURUS = Decimal("0.01")
HUNDRED = Decimal(100)
ZERO = Decimal("0.00")


def kurus(amount: Decimal) -> Decimal:
    return amount.quantize(KURUS, rounding=ROUND_HALF_UP)


def split_kdv(incl: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """`(exclusive, kdv)` of a KDV-inclusive amount at `rate` percent."""
    incl = kurus(incl)
    excl = kurus(incl * HUNDRED / (HUNDRED + rate))
    return excl, incl - excl


@dataclass(frozen=True)
class LineSnapshot:
    """What an Order line froze at placement; never recomputed afterwards."""

    quantity: int
    unit_price_incl: Decimal
    kdv_rate: Decimal
    total_incl: Decimal
    total_excl: Decimal
    total_kdv: Decimal


@dataclass(frozen=True)
class RateTotals:
    excl: Decimal
    kdv: Decimal
    incl: Decimal


@dataclass(frozen=True)
class OrderTotals:
    total_incl: Decimal
    total_excl: Decimal
    total_kdv: Decimal
    by_rate: dict[Decimal, RateTotals]


def line_snapshot(
    *,
    unit_price: Decimal,
    quantity: int,
    kdv_rate: Decimal,
) -> LineSnapshot:
    if quantity < 1:
        msg = "quantity must be at least 1"
        raise ValueError(msg)
    unit = kurus(unit_price)
    total = unit * quantity
    excl, kdv = split_kdv(total, kdv_rate)
    return LineSnapshot(
        quantity=quantity,
        unit_price_incl=unit,
        kdv_rate=kdv_rate,
        total_incl=total,
        total_excl=excl,
        total_kdv=kdv,
    )


def fee_rate() -> Decimal:
    """KDV rate for shipping and COD lines (Store settings will override this)."""
    return Decimal(str(settings.FEE_KDV_RATE))


def fee_line(amount: Decimal, *, kdv_rate: Decimal | None = None) -> LineSnapshot:
    """A shipping or COD fee as a single-quantity line."""
    rate = fee_rate() if kdv_rate is None else kdv_rate
    return line_snapshot(unit_price=amount, quantity=1, kdv_rate=rate)


def order_totals(lines: list[LineSnapshot]) -> OrderTotals:
    by_rate: dict[Decimal, RateTotals] = {}
    for line in lines:
        prev = by_rate.get(line.kdv_rate, RateTotals(ZERO, ZERO, ZERO))
        by_rate[line.kdv_rate] = RateTotals(
            excl=prev.excl + line.total_excl,
            kdv=prev.kdv + line.total_kdv,
            incl=prev.incl + line.total_incl,
        )
    return OrderTotals(
        total_incl=sum((line.total_incl for line in lines), ZERO),
        total_excl=sum((line.total_excl for line in lines), ZERO),
        total_kdv=sum((line.total_kdv for line in lines), ZERO),
        by_rate=by_rate,
    )
