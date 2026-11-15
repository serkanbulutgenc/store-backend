"""Voucher management for the Owner (ADR 0006). Staff have no access."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal  # noqa: TC003
from typing import Literal

from ninja import Router
from ninja import Schema

from store_backend.api.money import Money  # noqa: TC001
from store_backend.api.permissions import requires
from store_backend.services import voucher_admin
from store_backend.staff.permissions import Perm

router = Router(tags=["vouchers"])


class VoucherIn(Schema):
    code: str
    kind: Literal["percentage", "fixed"]
    value: Decimal
    minimum: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    cap: int | None = None
    once_per_customer: bool = False


class VoucherPatch(Schema):
    """What the Owner changes; anything left out stays as it is."""

    code: str | None = None
    value: Decimal | None = None
    minimum: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    cap: int | None = None
    once_per_customer: bool | None = None


class VoucherAdminOut(Schema):
    id: int
    code: str
    kind: Literal["percentage", "fixed"]
    value: Money
    minimum: Money | None
    starts_at: datetime
    ends_at: datetime | None
    cap: int | None
    once_per_customer: bool
    redemptions: int
    status: str


@router.get(
    "/vouchers",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_list",
    response=list[VoucherAdminOut],
)
def list_vouchers(request, status: str | None = None):
    return voucher_admin.listing(status)


@router.post(
    "/vouchers",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_create",
    response={201: VoucherAdminOut},
)
def create_voucher(request, data: VoucherIn):
    voucher = voucher_admin.create(request.auth, **data.model_dump())
    return 201, voucher_admin.view(voucher)


@router.patch(
    "/vouchers/{voucher_id}",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_edit",
    response=VoucherAdminOut,
)
def edit_voucher(request, voucher_id: int, data: VoucherPatch):
    voucher = voucher_admin.edit(
        request.auth,
        voucher_id,
        **data.model_dump(exclude_unset=True),
    )
    return voucher_admin.view(voucher)


@router.post(
    "/vouchers/{voucher_id}/deactivate",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_deactivate",
    response=VoucherAdminOut,
)
def deactivate_voucher(request, voucher_id: int):
    return voucher_admin.view(voucher_admin.deactivate(request.auth, voucher_id))


@router.post(
    "/vouchers/{voucher_id}/activate",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_activate",
    response=VoucherAdminOut,
)
def activate_voucher(request, voucher_id: int):
    return voucher_admin.view(voucher_admin.activate(request.auth, voucher_id))


@router.delete(
    "/vouchers/{voucher_id}",
    auth=requires(Perm.MANAGE_VOUCHERS),
    operation_id="vouchers_delete",
    response={204: None},
)
def delete_voucher(request, voucher_id: int):
    voucher_admin.delete(request.auth, voucher_id)
    return 204, None
