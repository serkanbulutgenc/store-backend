"""Roles and two-factor status of Dashboard users."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Literal

from allauth.mfa.models import Authenticator
from django.conf import settings

from store_backend.staff.permissions import APP_LABEL
from store_backend.staff.permissions import OWNER_GROUP
from store_backend.staff.permissions import STAFF_GROUP

if TYPE_CHECKING:
    from store_backend.users.models import User


def role(user: User) -> Literal["owner", "staff"] | None:
    """`"owner"`, `"staff"` or None. Owner wins if a user is somehow in both."""
    names = set(user.groups.values_list("name", flat=True))
    if OWNER_GROUP in names or user.is_superuser:
        return "owner"
    if STAFF_GROUP in names:
        return "staff"
    return None


def permissions(user: User) -> list[str]:
    """Codenames of the Dashboard permissions the user holds, sorted."""
    prefix = f"{APP_LABEL}."
    return sorted(
        p.removeprefix(prefix)
        for p in user.get_all_permissions()
        if p.startswith(prefix)
    )


def mfa_required(user: User) -> bool:
    """TOTP is required for the Owner, optional for Staff.

    `OWNER_MFA_REQUIRED=False` is for the local development stack only.
    """
    return settings.OWNER_MFA_REQUIRED and role(user) == "owner"


def mfa_enrolled(user: User) -> bool:
    return Authenticator.objects.filter(
        user=user,
        type=Authenticator.Type.TOTP,
    ).exists()


def mfa_blocked(user: User) -> bool:
    """An Owner who has not enrolled TOTP may not use the Dashboard API."""
    return mfa_required(user) and not mfa_enrolled(user)


def usable_permissions(user: User) -> list[str]:
    """What the user may do right now: nothing while two-factor is outstanding."""
    return [] if mfa_blocked(user) else permissions(user)
