# ruff: noqa: TC001
"""Owner-only Store settings endpoints for the Dashboard."""

from __future__ import annotations

from ninja import Router
from ninja import Schema

from store_backend.api.errors import UnprocessableError
from store_backend.api.money import Money
from store_backend.api.permissions import requires
from store_backend.services import shop_settings
from store_backend.staff.permissions import Perm

router = Router(tags=["settings"])


class SettingsOut(Schema):
    shipping_fee: Money
    free_shipping_threshold: Money | None
    cod_fee: Money
    fee_kdv_rate: str
    low_stock_default: int
    bank_name: str
    bank_account_holder: str
    bank_iban: str
    notification_emails: list[str]
    banner_image_url: str
    banner_link: str
    banner_alt: str
    seller_name: str
    seller_tax_number: str
    seller_address: str
    seller_email: str
    seller_phone: str
    return_carrier: str
    complaint_contact: str
    legal_versions: dict[str, str]

    @staticmethod
    def resolve_fee_kdv_rate(obj) -> str:
        return str(obj.fee_kdv_rate)


class SettingsPatch(Schema):
    """Every field optional; only the ones sent change. Money arrives as strings."""

    shipping_fee: str | None = None
    free_shipping_threshold: str | None = None
    cod_fee: str | None = None
    fee_kdv_rate: str | None = None
    low_stock_default: int | None = None
    bank_name: str | None = None
    bank_account_holder: str | None = None
    bank_iban: str | None = None
    notification_emails: list[str] | None = None
    banner_image_url: str | None = None
    banner_link: str | None = None
    banner_alt: str | None = None
    seller_name: str | None = None
    seller_tax_number: str | None = None
    seller_address: str | None = None
    seller_email: str | None = None
    seller_phone: str | None = None
    return_carrier: str | None = None
    complaint_contact: str | None = None
    legal_versions: dict[str, str] | None = None


@router.get(
    "/settings",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="settings_get",
    response=SettingsOut,
)
def get_settings(request):
    return shop_settings.get()


@router.patch(
    "/settings",
    auth=requires(Perm.MANAGE_SETTINGS),
    operation_id="settings_update",
    response=SettingsOut,
)
def update_settings(request, data: SettingsPatch):
    sent = data.model_dump(exclude_unset=True)
    # Null clears the free-shipping threshold; for any other field it is an error.
    nulls = [k for k, v in sent.items() if v is None and k != "free_shipping_threshold"]
    if nulls:
        msg = "Bu alan boş bırakılamaz."
        raise UnprocessableError(msg, fields={k: [msg] for k in nulls})
    return shop_settings.update(request.auth, **sent)
