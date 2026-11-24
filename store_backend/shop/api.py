# ruff: noqa: TC001, TC003
"""Owner-only Dashboard endpoints: Store settings and account-deletion requests."""

from __future__ import annotations

from datetime import datetime

from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.errors import UnprocessableError
from store_backend.api.money import Money
from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import customers
from store_backend.services import legal_pages
from store_backend.services import shop_settings
from store_backend.staff.permissions import Perm

router = Router(tags=["settings"])


class BankAccountIn(Schema):
    name: str
    account_holder: str
    iban: str


class BankAccountOut(Schema):
    name: str
    account_holder: str
    iban: str


class SettingsOut(Schema):
    shipping_fee: Money
    cod_fee: Money
    fee_kdv_rate: str
    low_stock_default: int
    stock_alerts_enabled: bool
    bank_accounts: list[BankAccountOut]
    notification_emails: list[str]
    seller_name: str
    seller_tax_number: str
    seller_address: str
    seller_email: str
    seller_phone: str
    return_carrier: str
    complaint_contact: str
    legal_versions: dict[str, str]

    @staticmethod
    def resolve_bank_accounts(obj) -> list:
        return shop_settings.bank_accounts()

    @staticmethod
    def resolve_legal_versions(obj) -> dict[str, str]:
        return legal_pages.current_versions()

    @staticmethod
    def resolve_fee_kdv_rate(obj) -> str:
        return str(obj.fee_kdv_rate)


class SettingsPatch(Schema):
    """Every field optional; only the ones sent change. Money arrives as strings."""

    shipping_fee: str | None = None
    cod_fee: str | None = None
    fee_kdv_rate: str | None = None
    low_stock_default: int | None = None
    stock_alerts_enabled: bool | None = None
    # The whole list, in order; it replaces the one stored.
    bank_accounts: list[BankAccountIn] | None = None
    notification_emails: list[str] | None = None
    seller_name: str | None = None
    seller_tax_number: str | None = None
    seller_address: str | None = None
    seller_email: str | None = None
    seller_phone: str | None = None
    return_carrier: str | None = None
    complaint_contact: str | None = None


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
    # Null is never a value: leave a field out to leave it alone.
    nulls = [k for k, v in sent.items() if v is None]
    if nulls:
        msg = "Bu alan boş bırakılamaz."
        raise UnprocessableError(msg, fields={k: [msg] for k in nulls})
    return shop_settings.update(request.auth, **sent)


class DeletionRequestOut(Schema):
    id: int
    email: str
    name: str
    status: str
    requested_at: datetime
    handled_at: datetime | None


@router.get(
    "/account-deletions",
    auth=requires(Perm.MANAGE_ACCOUNT_DELETIONS),
    operation_id="account_deletions_list",
    response=list[DeletionRequestOut],
)
@paginate(PageOffsetPagination)
def list_deletions(request):
    """The Owner queue: pending requests, oldest first."""
    return customers.pending_deletions()


@router.post(
    "/account-deletions/{request_id}/complete",
    auth=requires(Perm.MANAGE_ACCOUNT_DELETIONS),
    operation_id="account_deletions_complete",
    response=DeletionRequestOut,
)
def complete_deletion(request, request_id: int):
    """Anonymise the account. Its Orders stay with the name and billing address
    frozen at placement."""
    return customers.complete_deletion(request.auth, request_id)


@router.post(
    "/account-deletions/{request_id}/decline",
    auth=requires(Perm.MANAGE_ACCOUNT_DELETIONS),
    operation_id="account_deletions_decline",
    response=DeletionRequestOut,
)
def decline_deletion(request, request_id: int):
    return customers.decline_deletion(request.auth, request_id)
