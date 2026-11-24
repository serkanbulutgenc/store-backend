"""Reading and changing the Store settings singleton."""

from __future__ import annotations

import re
from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.shop.models import BankAccount
from store_backend.shop.models import StoreSettings

if TYPE_CHECKING:
    from store_backend.users.models import User

MONEY_FIELDS = ("shipping_fee", "cod_fee")
MAX_KDV_RATE = Decimal(100)
MAX_MONEY = Decimal("99999999.99")
MAX_LOW_STOCK = 10_000
MAX_EMAILS = 20
MAX_BANK_ACCOUNTS = 10
MAX_BANK_NAME = 100
MAX_ACCOUNT_HOLDER = 150
IBAN = re.compile(r"^TR\d{24}$")
EDITABLE = (
    *MONEY_FIELDS,
    "fee_kdv_rate",
    "low_stock_default",
    "stock_alerts_enabled",
    "notification_emails",
    "seller_name",
    "seller_tax_number",
    "seller_address",
    "seller_email",
    "seller_phone",
    "return_carrier",
    "complaint_contact",
)


def _row(*, lock: bool = False) -> StoreSettings:
    qs = StoreSettings.objects.select_for_update() if lock else StoreSettings.objects
    obj, created = qs.get_or_create(pk=1)
    if created:
        obj.refresh_from_db()  # DB-normalised values, not the Python defaults
    return obj


def get() -> StoreSettings:
    return _row()


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _decimal(field: str, value: Any, *, upper: Decimal | None = None) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise _invalid(field, "Geçerli bir tutar girin.") from None
    cap = upper if upper is not None else MAX_MONEY
    if not d.is_finite() or d < 0 or d > cap:
        raise _invalid(field, "Geçerli bir tutar girin.")
    return d.quantize(Decimal("0.01"))


def _clean(field: str, value: Any) -> Any:  # noqa: C901, PLR0911, PLR0912
    if field in MONEY_FIELDS:
        return _decimal(field, value)
    if field == "fee_kdv_rate":
        return _decimal(field, value, upper=MAX_KDV_RATE)
    if field == "low_stock_default":
        if not isinstance(value, int) or not 0 <= value <= MAX_LOW_STOCK:
            raise _invalid(field, "Eşik 0 ile 10000 arasında olmalı.")
        return value
    if field == "stock_alerts_enabled":
        if not isinstance(value, bool):
            raise _invalid(field, "Geçerli bir değer girin.")
        return value
    if field == "notification_emails":
        if len(value) > MAX_EMAILS:
            raise _invalid(field, "En fazla 20 alıcı girilebilir.")
        emails = [str(e).strip().lower() for e in value]
        for e in emails:
            try:
                validate_email(e)
            except ValidationError:
                raise _invalid(field, f"Geçersiz e-posta adresi: {e}") from None
        return list(dict.fromkeys(emails))
    if field == "seller_email":
        email = str(value).strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise _invalid(field, "Geçerli bir e-posta adresi girin.") from None
        return email
    text = str(value).strip()
    limit = StoreSettings._meta.get_field(field).max_length  # noqa: SLF001
    if limit and len(text) > limit:
        raise _invalid(field, f"En fazla {limit} karakter girilebilir.")
    return text


def bank_accounts() -> list[BankAccount]:
    """The accounts a Shopper may pay into, in the order the Owner set."""
    return list(BankAccount.objects.all())


def _account_rows(accounts: Any) -> list[dict[str, str]]:
    """Check the Owner's list and return it cleaned, in order."""
    field = "bank_accounts"
    if not isinstance(accounts, list):
        raise _invalid(field, "Geçerli bir hesap listesi girin.")
    if len(accounts) > MAX_BANK_ACCOUNTS:
        raise _invalid(field, f"En fazla {MAX_BANK_ACCOUNTS} hesap girilebilir.")
    rows: list[dict[str, str]] = []
    for number, raw in enumerate(accounts, start=1):
        where = f"{number}. hesap: "
        data = raw if isinstance(raw, dict) else {}
        name = str(data.get("name", "")).strip()
        holder = str(data.get("account_holder", "")).strip()
        iban = re.sub(r"\s+", "", str(data.get("iban", ""))).upper()
        if not name:
            raise _invalid(field, where + "Banka adı gerekli.")
        if not holder:
            raise _invalid(field, where + "Hesap sahibi gerekli.")
        if not IBAN.match(iban):
            raise _invalid(
                field,
                where + "Geçerli bir IBAN girin (TR ile başlayan 26 karakter).",
            )
        if len(name) > MAX_BANK_NAME or len(holder) > MAX_ACCOUNT_HOLDER:
            msg = f"Banka adı en fazla {MAX_BANK_NAME}, hesap sahibi en fazla "
            raise _invalid(
                field,
                where + msg + f"{MAX_ACCOUNT_HOLDER} karakter olabilir.",
            )
        rows.append({"name": name, "account_holder": holder, "iban": iban})
    ibans = [r["iban"] for r in rows]
    if len(set(ibans)) != len(ibans):
        raise _invalid(field, "Aynı IBAN birden fazla kez girilemez.")
    return rows


def _account_snapshot() -> list[dict[str, str]]:
    return [
        {"name": a.name, "account_holder": a.account_holder, "iban": a.iban}
        for a in bank_accounts()
    ]


def _snapshot(settings: StoreSettings, fields) -> dict[str, Any]:
    return {
        f: str(v) if isinstance(v := getattr(settings, f), Decimal) else v
        for f in fields
    }


@transaction.atomic
def update(actor: User, **changes: Any) -> StoreSettings:
    """Apply a partial update and audit what changed."""
    accounts = changes.pop("bank_accounts", None)
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen ayar.")
    settings = _row(lock=True)
    cleaned = {f: _clean(f, v) for f, v in changes.items()}
    changed = [f for f, v in cleaned.items() if getattr(settings, f) != v]
    rows = None if accounts is None else _account_rows(accounts)
    accounts_before = _account_snapshot()
    accounts_changed = rows is not None and rows != accounts_before
    if not changed and not accounts_changed:
        return settings
    before = _snapshot(settings, changed)
    after_extra: dict[str, Any] = {}
    if changed:
        for f in changed:
            setattr(settings, f, cleaned[f])
        settings.save(update_fields=changed)
    if accounts_changed and rows is not None:
        BankAccount.objects.all().delete()
        BankAccount.objects.bulk_create(
            [BankAccount(position=i, **row) for i, row in enumerate(rows)],
        )
        before["bank_accounts"] = accounts_before
        after_extra["bank_accounts"] = rows
        changed = [*changed, "bank_accounts"]
    scalars = [f for f in changed if f != "bank_accounts"]
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=("shop.storesettings", settings.pk),
        summary="Ayarlar değişti: " + ", ".join(changed),
        before=before,
        after={**_snapshot(settings, scalars), **after_extra},
    )
    return settings
