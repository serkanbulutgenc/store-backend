"""Voucher codes: finding the Voucher a Shopper typed and checking it may be used."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from django.db.models import Q
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.voucher.models import Voucher

if TYPE_CHECKING:
    from store_backend.users.models import User


class Reason(StrEnum):
    """Why a Shopper cannot use a code. The Storefront words each one in Turkish."""

    INVALID = "invalid"
    EXPIRED = "expired"
    INACTIVE = "inactive"
    EXHAUSTED = "exhausted"
    ALREADY_USED = "already_used"
    MINIMUM = "minimum"


class VoucherError(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason = reason


def _used_by(voucher: Voucher, user: User | None, email: str) -> bool:
    """Whether this Customer, or whoever used this email as a Guest, redeemed it."""
    who = Q(order__guest_email__iexact=email.strip())
    if user is not None:
        who |= Q(user=user)
    return voucher.applications.filter(who).exists()


def validate(
    code: str,
    *,
    user: User | None,
    email: str,
    lock: bool = False,
) -> Voucher:
    """The Voucher for `code`, or an error saying why the Shopper cannot use it.

    `lock` holds the Voucher row until the transaction ends, so a placement counts its
    Redemptions after any concurrent one has committed. Needs a transaction.
    """
    found = Voucher.objects.filter(code__iexact=code.strip())
    voucher = (found.select_for_update() if lock else found).first()
    if voucher is None:
        raise VoucherError(Reason.INVALID)
    if not voucher.is_active():
        raise VoucherError(Reason.EXPIRED)
    if not voucher.offers.filter(status=ConditionalOffer.OPEN).exists():
        raise VoucherError(Reason.INACTIVE)
    cap = voucher.offers.get().max_global_applications
    if cap is not None and voucher.applications.count() >= cap:
        raise VoucherError(Reason.EXHAUSTED)
    if voucher.usage == Voucher.ONCE_PER_CUSTOMER and _used_by(voucher, user, email):
        raise VoucherError(Reason.ALREADY_USED)
    return voucher
