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
    # Revenue data, so the Owner alone (see the glossary).
    Widget("report_summary", None, owner_only=True),
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


@dataclass(frozen=True)
class Layout:
    keys: list[str]
    collapsed: list[str]
    is_default: bool


def current(user) -> Layout:
    """What the user sees: the Widgets on in order, which of them are folded, and
    whether this is still the role default. Limited to what they may use right now."""
    allowed = {w.key for w in available(user)}
    saved = HomeLayout.objects.filter(user=user).first()
    if saved is None:
        return Layout(default_keys(user), [], is_default=True)
    keys = [k for k in saved.widgets if k in allowed]
    return Layout(keys, [k for k in saved.collapsed if k in keys], is_default=False)


def save(user, keys: Iterable[str], collapsed: Iterable[str] | None = None) -> None:
    """Store the Widgets that are on, in order. `collapsed` says which are folded; leave
    it out to keep what was folded before. A folded key that is not on is dropped."""
    keys = list(keys)
    allowed = {w.key for w in available(user)}
    given = None if collapsed is None else list(collapsed)
    unknown = [k for k in [*keys, *(given or [])] if k not in allowed]
    if unknown:
        msg = f"Bu bileşenler kullanılamaz: {', '.join(dict.fromkeys(unknown))}."
        raise UnprocessableError(msg, code="widget_not_available")
    if len(set(keys)) != len(keys):
        msg = "Bir bileşen yalnızca bir kez eklenebilir."
        raise UnprocessableError(msg, code="widget_duplicated")
    if given is None:
        before = HomeLayout.objects.filter(user=user).first()
        given = before.collapsed if before else []
    HomeLayout.objects.update_or_create(
        user=user,
        defaults={
            "widgets": keys,
            "collapsed": [k for k in dict.fromkeys(given) if k in keys],
        },
    )


def reset(user) -> None:
    HomeLayout.objects.filter(user=user).delete()
