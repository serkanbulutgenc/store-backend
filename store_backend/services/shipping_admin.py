# ruff: noqa: EM101, TRY003
"""Owner edits to the rate table, zone names, province mapping and class desi."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING

from django.db import transaction

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.shop.models import ProvinceZone
from store_backend.shop.models import RateRow
from store_backend.shop.models import Zone

if TYPE_CHECKING:
    from store_backend.users.models import User

PRICE_FIELDS = ("band_0_2", "band_3_5", "band_6_10", "per_extra_desi")
BANDS = PRICE_FIELDS[:3]
MAX_PRICE = Decimal("99999999.99")
MAX_DESI = 1000
MAX_ZONE_NAME = 64


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _price(field: str, value: object) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise _invalid(field, "Geçerli bir tutar girin.") from None
    # Zero is refused: a free row would always win the cheapest-carrier pick.
    if not Decimal(0) < amount <= MAX_PRICE:
        raise _invalid(field, "Tutar sıfırdan büyük olmalı.")
    return amount


def _snapshot(row: RateRow, fields: list[str]) -> dict[str, str]:
    return {f: str(getattr(row, f)) for f in fields}


@transaction.atomic
def update_rate(actor: User, rate_id: int, **changes: object) -> RateRow:
    unknown = set(changes) - set(PRICE_FIELDS)
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    blank = [f for f, v in changes.items() if v is None]
    if blank:
        msg = "Bu alan boş bırakılamaz."
        raise UnprocessableError(msg, fields={f: [msg] for f in blank})
    row = (
        RateRow.objects.select_for_update()
        .select_related("carrier", "zone")
        .filter(
            pk=rate_id,
        )
        .first()
    )
    if row is None:
        raise NotFoundError("Fiyat satırı bulunamadı.")
    cleaned = {f: _price(f, v) for f, v in changes.items()}
    changed = [f for f, v in cleaned.items() if getattr(row, f) != v]
    if not changed:
        return row
    before = _snapshot(row, changed)
    for f in changed:
        setattr(row, f, cleaned[f])
    # Heavier bands never cost less than lighter ones.
    if not row.band_0_2 <= row.band_3_5 <= row.band_6_10:
        field = next((f for f in BANDS if f in changed), BANDS[0])
        raise _invalid(field, "Fiyatlar desi aralığına göre artmalı.")
    row.save(update_fields=changed)
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=row,
        summary=f"Kargo fiyatı: {row.carrier} / {row.zone}",
        before=before,
        after=_snapshot(row, changed),
    )
    return row


@transaction.atomic
def move_province(actor: User, province_id: int, zone_id: int) -> ProvinceZone:
    province = (
        ProvinceZone.objects.select_for_update()
        .select_related("zone")
        .filter(pk=province_id)
        .first()
    )
    if province is None:
        raise NotFoundError("İl bulunamadı.")
    zone = Zone.objects.filter(pk=zone_id).first()
    if zone is None:
        raise _invalid("zone_id", "Bölge bulunamadı.")
    if zone.pk == province.zone_id:
        return province
    before = {"zone": province.zone.number}
    province.zone = zone
    province.save(update_fields=["zone"])
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=province,
        summary=f"İl bölgesi: {province.name}",
        before=before,
        after={"zone": zone.number},
    )
    return province


@transaction.atomic
def rename_zone(actor: User, zone_id: int, name: str) -> Zone:
    zone = Zone.objects.select_for_update().filter(pk=zone_id).first()
    if zone is None:
        raise NotFoundError("Bölge bulunamadı.")
    name = name.strip()
    if len(name) > MAX_ZONE_NAME:
        raise _invalid("name", "Ad çok uzun.")
    if name != zone.name:
        before = {"name": zone.name}
        label = str(zone)
        zone.name = name
        zone.save(update_fields=["name"])
        audit.record(
            actor,
            audit.Action.SETTINGS_CHANGE,
            target=zone,
            summary=f"Bölge adı: {label}",
            before=before,
            after={"name": name},
        )
    return zone


def validate_desi(value: object, field: str = "desi") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(field, "Desi tam sayı olmalı.")
    if not 1 <= value <= MAX_DESI:
        raise _invalid(field, f"Desi 1 ile {MAX_DESI} arasında olmalı.")
    return value
