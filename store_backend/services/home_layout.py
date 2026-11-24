"""Which Widgets a Dashboard user sees on the Ana sayfa, and in what order.

The catalogue is fixed in code (ADR 0010): each Widget names the permission it needs and
whether Staff get it by default. A person's saved layout is only ever a list of keys; it
is checked against the catalogue and their permissions every time it is read, so a
revoked permission or a retired key never reaches the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from store_backend.api.errors import UnprocessableError
from store_backend.services import accounts
from store_backend.staff.models import HomeLayout
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Widget:
    key: str
    #: What the user must hold; None for a Widget that is only for the Owner.
    permission: Perm | None
    #: Owner-only (Revenue data or an Owner decision): never offered to Staff.
    owner_only: bool = False


#: In the order they are offered. Add a Widget here, give it a builder in `home.py`.
CATALOGUE: tuple[Widget, ...] = (
    Widget("approvals", None, owner_only=True),
    Widget("contact_replies", Perm.MANAGE_CONTACT, owner_only=True),
    Widget("new_orders", Perm.VIEW_ORDERS),
    Widget("unpaid_transfers_near_expiry", Perm.VIEW_ORDERS),
    Widget("open_returns", Perm.VIEW_ORDERS),
    Widget("low_stock", Perm.VIEW_PRODUCTS),
)


def available(user) -> list[Widget]:
    """Widgets this user may turn on: they hold the permission, and the Owner-only ones
    only for the Owner."""
    owner = accounts.role(user) == "owner"
    return [
        w
        for w in CATALOGUE
        if (owner or not w.owner_only)
        and (w.permission is None or user.has_perm(w.permission.permission_name))
    ]


def default_keys(user) -> list[str]:
    """What a user who never configured sees: every Widget they may use."""
    return [w.key for w in available(user)]


def current(user) -> tuple[list[str], bool]:
    """`(enabled keys in order, is_default)`, limited to what the user may use now."""
    allowed = {w.key for w in available(user)}
    saved = HomeLayout.objects.filter(user=user).first()
    if saved is None:
        return default_keys(user), True
    return [k for k in saved.widgets if k in allowed], False


def save(user, keys: Iterable[str]) -> None:
    keys = list(keys)
    allowed = {w.key for w in available(user)}
    unknown = [k for k in keys if k not in allowed]
    if unknown:
        msg = f"Bu bileşenler kullanılamaz: {', '.join(unknown)}."
        raise UnprocessableError(msg, code="widget_not_available")
    if len(set(keys)) != len(keys):
        msg = "Bir bileşen yalnızca bir kez eklenebilir."
        raise UnprocessableError(msg, code="widget_duplicated")
    HomeLayout.objects.update_or_create(user=user, defaults={"widgets": keys})


def reset(user) -> None:
    HomeLayout.objects.filter(user=user).delete()
