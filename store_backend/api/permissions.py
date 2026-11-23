"""Permission checks for the Dashboard API.

Every admin endpoint declares exactly one named permission:

    @router.post("/orders/{number}/pay", auth=requires(Perm.MARK_PAID), ...)

The API-level default is `UndeclaredPermission`, which denies everything, so an
endpoint that forgets to declare one fails closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja.security.base import AuthBase

from store_backend.api.errors import ForbiddenError
from store_backend.services import accounts
from store_backend.staff.permissions import OWNER_GROUP
from store_backend.users.middleware import TOKEN_HEADER

if TYPE_CHECKING:
    from django.http import HttpRequest

    from store_backend.staff.permissions import Perm


class _TokenHeaderAuth(AuthBase):
    openapi_type = "apiKey"
    openapi_in = "header"
    openapi_name = TOKEN_HEADER


class RequiresPermission(_TokenHeaderAuth):
    """A signed-in, active Staff member holding `permission`.

    Identified by `X-Session-Token` only (a Django session cookie is not enough).
    An Owner who has not enrolled TOTP is refused everywhere except where the
    endpoint sets `allow_unenrolled_owner` (`/me`, so the UI can start enrolment).
    """

    def __init__(
        self,
        permission: Perm | None,
        *,
        allow_unenrolled_owner: bool = False,
    ) -> None:
        self.permission = permission
        self.allow_unenrolled_owner = allow_unenrolled_owner
        super().__init__()

    def __call__(self, request: HttpRequest):
        if not getattr(request, "authenticated_by_session_token", False):
            return None  # Ninja answers 401
        user = request.user
        if not user.is_active:
            return None
        if not user.is_staff:
            raise ForbiddenError
        if not self.allow_unenrolled_owner and accounts.mfa_blocked(user):
            msg = "Panele girmek için iki adımlı doğrulamayı etkinleştirmelisiniz."
            raise ForbiddenError(msg, code="mfa_enrolment_required")
        if not self.allows(user):
            raise ForbiddenError
        return user

    def allows(self, user) -> bool:
        return self.permission is None or user.has_perm(
            self.permission.permission_name,
        )


class RequiresOwner(RequiresPermission):
    """The Owner group itself, for a screen that is not a grantable permission."""

    def __init__(self) -> None:
        super().__init__(None)

    def allows(self, user) -> bool:
        return user.groups.filter(name=OWNER_GROUP).exists()


def requires_owner() -> RequiresOwner:
    return RequiresOwner()


def requires(
    permission: Perm | None = None,
    *,
    allow_unenrolled_owner: bool = False,
) -> RequiresPermission:
    return RequiresPermission(
        permission,
        allow_unenrolled_owner=allow_unenrolled_owner,
    )


class UndeclaredPermission(_TokenHeaderAuth):
    """Default for the admin API: an endpoint without `auth=requires(...)` is closed."""

    def __call__(self, request: HttpRequest):
        msg = "Bu uç nokta için yetki tanımlanmamış."
        raise ForbiddenError(msg, code="permission_not_declared")
