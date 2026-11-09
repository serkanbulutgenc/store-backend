"""Staff-only Dashboard API at /api/admin/.

Each endpoint declares one named permission (ticket 08); until then the only gate is
an active staff session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja.security import SessionAuth

from store_backend.api.base import create_api
from store_backend.api.errors import ForbiddenError

if TYPE_CHECKING:
    from django.http import HttpRequest


class StaffSessionAuth(SessionAuth):
    def authenticate(self, request: HttpRequest, key: str | None):
        user = super().authenticate(request, key)
        if user is None:
            return None  # anonymous: Ninja answers 401
        if not user.is_staff:
            # Signed in, but not allowed in the Dashboard.
            raise ForbiddenError
        return user


admin_api = create_api(
    title="Aura Tekstil Admin API",
    urls_namespace="admin_api",
    auth=StaffSessionAuth(),
)
