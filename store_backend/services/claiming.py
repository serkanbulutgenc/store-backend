"""Claiming: attaching guest Orders to the account that verified their email."""

from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.models import EmailAddress
from oscar.apps.order.models import Order

if TYPE_CHECKING:
    from store_backend.users.models import User


def claim_orders(user: User, email: str) -> int:
    """Attach guest Orders placed with `email` to `user`. Never re-assigns an owned
    Order. The caller vouches that `email` is a verified address of `user`.
    """
    return Order.objects.filter(user__isnull=True, guest_email__iexact=email).update(
        user=user,
    )


def verified_owner(email: str) -> User | None:
    """The active account that has verified `email`, if any (for new guest Orders)."""
    address = (
        EmailAddress.objects.filter(email__iexact=email.strip(), verified=True)
        .select_related("user")
        .first()
    )
    if address and address.user.is_active and address.user.anonymised_at is None:
        return address.user
    return None
