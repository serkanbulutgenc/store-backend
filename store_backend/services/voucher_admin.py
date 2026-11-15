"""The Owner's Voucher management: create, edit, deactivate and delete (ADR 0006).

A Voucher is Oscar's `Voucher` plus exactly one `ConditionalOffer` whose benefit and
condition say what it takes off and from what basket total. The Storefront relies on
that shape, so everything that writes one goes through here.
"""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import Condition
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.offer.models import Range
from oscar.apps.voucher.models import Voucher

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit

if TYPE_CHECKING:
    from store_backend.users.models import User

CODE_SHAPE = re.compile(r"[A-Z0-9-]{3,20}")
KINDS = {"percentage": Benefit.PERCENTAGE, "fixed": Benefit.FIXED}
STATUSES = ("active", "inactive", "expired", "scheduled", "exhausted")
ALL_PRODUCTS = "Tüm ürünler"
# Oscar needs an end date: "no expiry" is stored as this far-off one, shown as empty.
NO_EXPIRY = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)


def _clean_code(code: str) -> str:
    """The code tidied up (spaces trimmed, capitals), then checked to be plain A-Z, 0-9
    and hyphens, 3 to 20 long, so a Shopper never meets an `i`/`İ` mismatch."""
    cleaned = code.strip().upper()
    if not CODE_SHAPE.fullmatch(cleaned):
        msg = (
            "Kupon kodu 3-20 karakter olmalı; yalnızca A-Z, 0-9 ve tire kullanılabilir."
        )
        raise UnprocessableError(msg, fields={"code": [msg]})
    return cleaned


def _check_code_is_free(code: str) -> None:
    """No two Vouchers share a code, whatever the case it was stored in."""
    if Voucher.objects.filter(code__iexact=code).exists():
        msg = "Bu kupon kodu zaten kullanılıyor."
        raise UnprocessableError(msg, fields={"code": [msg]})


def _check_value(kind: str, value: Decimal) -> None:
    """A percentage is 1 to 100; a fixed amount is more than nothing."""
    ok = Decimal(1) <= value <= Decimal(100) if kind == "percentage" else value > 0
    if not ok:
        msg = (
            "Yüzde 1 ile 100 arasında olmalı."
            if kind == "percentage"
            else "Tutar sıfırdan büyük olmalı."
        )
        raise UnprocessableError(msg, fields={"value": [msg]})


def _check_limits(
    minimum: Decimal | None,
    cap: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    """No negative minimum, a cap of at least 1, an end after the start."""
    problems: dict[str, list[str]] = {}
    if minimum is not None and minimum < 0:
        problems["minimum"] = ["Alt sınır sıfırdan küçük olamaz."]
    if cap is not None and cap < 1:
        problems["cap"] = ["Kullanım sınırı en az 1 olmalı."]
    if starts_at and ends_at and ends_at <= starts_at:
        problems["ends_at"] = ["Bitiş, başlangıçtan sonra olmalı."]
    if problems:
        msg = next(iter(problems.values()))[0]
        raise UnprocessableError(msg, fields=problems)


def _everything() -> Range:
    """The one shared range that includes every product; nothing ever edits it."""
    return Range.objects.get_or_create(
        name=ALL_PRODUCTS,
        defaults={"includes_all_products": True},
    )[0]


@transaction.atomic
def create(  # noqa: PLR0913
    actor: User,
    *,
    code: str,
    kind: str,
    value: Decimal,
    minimum: Decimal | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    cap: int | None = None,
    once_per_customer: bool = False,
) -> Voucher:
    code = _clean_code(code)
    _check_code_is_free(code)
    _check_value(kind, value)
    _check_limits(minimum, cap, starts_at, ends_at)
    everything = _everything()
    offer = ConditionalOffer.objects.create(
        name=f"Kupon {code}",
        offer_type=ConditionalOffer.VOUCHER,
        condition=Condition.objects.create(
            range=everything,
            type=Condition.VALUE,
            value=minimum or Decimal(0),
        ),
        benefit=Benefit.objects.create(
            range=everything,
            type=KINDS[kind],
            value=value,
        ),
        max_global_applications=cap,
    )
    voucher = Voucher.objects.create(
        name=code,
        code=code,
        usage=Voucher.ONCE_PER_CUSTOMER if once_per_customer else Voucher.MULTI_USE,
        start_datetime=starts_at or timezone.now(),
        end_datetime=ends_at or NO_EXPIRY,
    )
    voucher.offers.add(offer)
    audit.record(
        actor,
        audit.Action.VOUCHER_CREATED,
        target=voucher,
        summary=f"Kupon oluşturuldu: {code}",
        after=_snapshot(voucher),
    )
    return voucher


def _get(voucher_id: int) -> Voucher:
    """The Voucher, held for the rest of the transaction, or a 404."""
    voucher = Voucher.objects.select_for_update().filter(pk=voucher_id).first()
    if voucher is None:
        msg = "Kupon bulunamadı."
        raise NotFoundError(msg)
    return voucher


def _check_locks(voucher: Voucher, changes: dict[str, Any]) -> None:
    """The code is free, and from the first Redemption on the code and the once-per-
    Customer switch stay as they are (sending them back unchanged is fine)."""
    new_code = changes.get("code", voucher.code)
    code_changes = new_code != voucher.code
    once = voucher.usage == Voucher.ONCE_PER_CUSTOMER
    switch_changes = changes.get("once_per_customer", once) != once
    if voucher.applications.exists():
        locked = {
            field: ["Bu alan ilk kullanımdan sonra değiştirilemez."]
            for field, differs in (
                ("code", code_changes),
                ("once_per_customer", switch_changes),
            )
            if differs
        }
        if locked:
            msg = next(iter(locked.values()))[0]
            raise UnprocessableError(msg, fields=locked)
    if code_changes:
        _check_code_is_free(new_code)


def _check_edit(voucher: Voucher, changes: dict[str, Any]) -> None:
    """Refuse an edit that breaks a lock or a limit, before anything is written."""
    if "code" in changes:
        changes["code"] = _clean_code(changes["code"])
    _check_locks(voucher, changes)
    result = {**view(voucher), **changes}
    _check_value(result["kind"], Decimal(result["value"]))
    _check_limits(
        result["minimum"],
        result["cap"],
        result["starts_at"],
        result["ends_at"],
    )
    if result["cap"] is not None and result["cap"] < result["redemptions"]:
        msg = "Kullanım sınırı, şimdiye kadarki kullanımdan az olamaz."
        raise UnprocessableError(msg, fields={"cap": [msg]})


def _apply_edit(voucher: Voucher, changes: dict[str, Any]) -> None:
    offer = voucher.offers.get()
    if "code" in changes:
        voucher.code = voucher.name = changes["code"]
        offer.name = f"Kupon {voucher.code}"
    if "once_per_customer" in changes:
        voucher.usage = (
            Voucher.ONCE_PER_CUSTOMER
            if changes["once_per_customer"]
            else Voucher.MULTI_USE
        )
    if "value" in changes:
        offer.benefit.value = changes["value"]
        offer.benefit.save()
    if "minimum" in changes:
        offer.condition.value = changes["minimum"] or Decimal(0)
        offer.condition.save()
    if "cap" in changes:
        offer.max_global_applications = changes["cap"]
    if "starts_at" in changes:
        voucher.start_datetime = changes["starts_at"]
    if "ends_at" in changes:
        voucher.end_datetime = changes["ends_at"] or NO_EXPIRY
    offer.save()
    voucher.save()


@transaction.atomic
def edit(actor: User, voucher_id: int, **changes: Any) -> Voucher:
    """Change a Voucher; what `changes` leaves out stays as it is, and `None` clears a
    minimum, an end or a cap. The locks and limits are checked on the result."""
    for required in ("starts_at", "value", "code", "once_per_customer"):
        if changes.get(required) is None:
            changes.pop(required, None)  # these always have a value: null means "as is"
    voucher = _get(voucher_id)
    before = _snapshot(voucher)
    _check_edit(voucher, changes)
    _apply_edit(voucher, changes)
    audit.record(
        actor,
        audit.Action.VOUCHER_EDITED,
        target=voucher,
        summary=f"Kupon düzenlendi: {voucher.code}",
        before=before,
        after=_snapshot(voucher),
    )
    return voucher


def deactivate(actor: User, voucher_id: int) -> Voucher:
    """Suspend the Voucher's offer, so the Storefront refuses its code."""
    return _set_active(actor, voucher_id, active=False)


def activate(actor: User, voucher_id: int) -> Voucher:
    """Reopen the offer. Dates and the cap still decide whether the code works."""
    return _set_active(actor, voucher_id, active=True)


@transaction.atomic
def _set_active(actor: User, voucher_id: int, *, active: bool) -> Voucher:
    voucher = _get(voucher_id)
    offer = voucher.offers.get()
    if (offer.status == ConditionalOffer.OPEN) == active:
        return voucher  # already so: nothing to change, nothing to audit
    offer.status = ConditionalOffer.OPEN if active else ConditionalOffer.SUSPENDED
    offer.save()
    audit.record(
        actor,
        audit.Action.VOUCHER_ACTIVATED if active else audit.Action.VOUCHER_DEACTIVATED,
        target=voucher,
        summary=f"Kupon {'açıldı' if active else 'kapatıldı'}: {voucher.code}",
        before={"active": not active},
        after={"active": active},
    )
    return voucher


@transaction.atomic
def delete(actor: User, voucher_id: int) -> None:
    """Remove a Voucher and its offer, benefit and condition; the shared range stays."""
    voucher = _get(voucher_id)
    if used := voucher.applications.count():
        msg = f"Bu kupon {used} kez kullanıldı; silinemez. Kapatabilirsiniz."
        raise ConflictError(msg, code="voucher_redeemed")
    offer = voucher.offers.get()
    audit.record(
        actor,
        audit.Action.VOUCHER_DELETED,
        target=voucher,
        summary=f"Kupon silindi: {voucher.code}",
        before=_snapshot(voucher),
    )
    voucher.delete()
    offer.delete()  # Oscar then drops its benefit and condition: nothing else uses them


def _snapshot(voucher: Voucher) -> dict:
    """What the audit log keeps of a Voucher: its settings as plain JSON."""
    shown = view(voucher)
    money = {
        k: None if shown[k] is None else f"{shown[k]:.2f}" for k in ("value", "minimum")
    }
    return {
        "code": shown["code"],
        "kind": shown["kind"],
        **money,
        "starts_at": shown["starts_at"].isoformat(),
        "ends_at": shown["ends_at"].isoformat() if shown["ends_at"] else None,
        "cap": shown["cap"],
        "once_per_customer": shown["once_per_customer"],
    }


def listing(status: str | None = None) -> list[dict]:
    """The Vouchers as the Dashboard shows them, the newest first; `status` narrows."""
    if status is not None and status not in STATUSES:
        msg = "Geçersiz durum."
        raise UnprocessableError(msg, fields={"status": [msg]})
    vouchers = Voucher.objects.order_by("-date_created", "-pk")
    rows = [view(voucher) for voucher in vouchers]
    return [row for row in rows if status is None or row["status"] == status]


def _status(voucher: Voucher, offer: ConditionalOffer, redemptions: int) -> str:
    """The first that applies: inactive, expired, scheduled, exhausted, else active."""
    now = timezone.now()
    if offer.status != ConditionalOffer.OPEN:
        return "inactive"
    if voucher.end_datetime <= now:
        return "expired"
    if voucher.start_datetime > now:
        return "scheduled"
    if offer.max_global_applications is not None and (
        redemptions >= offer.max_global_applications
    ):
        return "exhausted"
    return "active"


def view(voucher: Voucher) -> dict:
    """The Voucher as the Dashboard shows it."""
    offer = voucher.offers.get()
    redemptions = voucher.applications.count()
    return {
        "id": voucher.pk,
        "code": voucher.code,
        "kind": "percentage" if offer.benefit.type == Benefit.PERCENTAGE else "fixed",
        "value": offer.benefit.value,
        "minimum": offer.condition.value or None,
        "starts_at": voucher.start_datetime,
        "ends_at": None if voucher.end_datetime >= NO_EXPIRY else voucher.end_datetime,
        "cap": offer.max_global_applications,
        "once_per_customer": voucher.usage == Voucher.ONCE_PER_CUSTOMER,
        "redemptions": redemptions,
        "status": _status(voucher, offer, redemptions),
    }
