"""Carrier rates: the cheapest carrier's fee for a destination province and desi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from store_backend.shop.models import ProvinceZone
from store_backend.shop.models import RateRow
from store_backend.shop.models import province_key

if TYPE_CHECKING:
    from decimal import Decimal

BAND_0_2_MAX = 2
BAND_3_5_MAX = 5
BAND_6_10_MAX = 10


@dataclass(frozen=True)
class ShippingFee:
    carrier: str
    fee: Decimal  # KDV-inclusive TRY


def _price(row: RateRow, desi: int) -> Decimal:
    if desi <= BAND_0_2_MAX:
        return row.band_0_2
    if desi <= BAND_3_5_MAX:
        return row.band_3_5
    if desi <= BAND_6_10_MAX:
        return row.band_6_10
    return row.band_6_10 + row.per_extra_desi * (desi - BAND_6_10_MAX)


def fee_for(desi: int | None, province: str | None) -> ShippingFee | None:
    """The cheapest carrier's fee, or None when the desi or province is unknown.

    Ties go to the carrier listed first (lowest position).
    """
    if not desi or desi < 1 or not province:
        return None
    key = province_key(province)
    if not key:
        return None
    zone_id = (
        ProvinceZone.objects.filter(key=key).values_list("zone_id", flat=True).first()
    )
    if zone_id is None:
        return None
    rows = list(
        RateRow.objects.filter(zone_id=zone_id)
        .select_related("carrier")
        .order_by("carrier__position", "carrier_id"),
    )
    if not rows:
        return None
    best = min(rows, key=lambda row: _price(row, desi))  # min keeps the first tie
    return ShippingFee(carrier=best.carrier.name, fee=_price(best, desi))
