# ruff: noqa: TC001
"""Owner-only Dashboard endpoints for carrier rates, zones and the province mapping."""

from __future__ import annotations

from ninja import Router
from ninja import Schema

from store_backend.api.money import Money
from store_backend.api.permissions import requires
from store_backend.services import shipping_admin
from store_backend.shop.models import Carrier
from store_backend.shop.models import ProvinceZone
from store_backend.shop.models import RateRow
from store_backend.shop.models import Zone
from store_backend.staff.permissions import Perm

router = Router(tags=["shipping"])


class CarrierOut(Schema):
    id: int
    name: str


class ZoneOut(Schema):
    id: int
    number: int
    name: str


class RateOut(Schema):
    id: int
    carrier_id: int
    zone_id: int
    band_0_2: Money
    band_3_5: Money
    band_6_10: Money
    per_extra_desi: Money


class ProvinceOut(Schema):
    id: int
    name: str
    zone_id: int


class ShippingOut(Schema):
    carriers: list[CarrierOut]
    zones: list[ZoneOut]
    rates: list[RateOut]
    provinces: list[ProvinceOut]


class RatePatch(Schema):
    """Prices arrive as strings; only the ones sent change."""

    band_0_2: str | None = None
    band_3_5: str | None = None
    band_6_10: str | None = None
    per_extra_desi: str | None = None


class ProvincePatch(Schema):
    zone_id: int


class ZonePatch(Schema):
    name: str


@router.get(
    "/shipping",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="shipping_get",
    response=ShippingOut,
)
def get_shipping(request):
    return {
        "carriers": Carrier.objects.all(),
        "zones": Zone.objects.all(),
        "rates": RateRow.objects.all(),
        "provinces": ProvinceZone.objects.all(),
    }


@router.patch(
    "/shipping/rates/{rate_id}",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="shipping_update_rate",
    response=RateOut,
)
def update_rate(request, rate_id: int, data: RatePatch):
    return shipping_admin.update_rate(
        request.auth,
        rate_id,
        **data.model_dump(exclude_unset=True),
    )


@router.patch(
    "/shipping/provinces/{province_id}",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="shipping_move_province",
    response=ProvinceOut,
)
def move_province(request, province_id: int, data: ProvincePatch):
    return shipping_admin.move_province(request.auth, province_id, data.zone_id)


@router.patch(
    "/shipping/zones/{zone_id}",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="shipping_rename_zone",
    response=ZoneOut,
)
def rename_zone(request, zone_id: int, data: ZonePatch):
    return shipping_admin.rename_zone(request.auth, zone_id, data.name)
