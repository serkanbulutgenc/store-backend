"""Auth classes for the store API.

- `CustomerAuth`: a signed-in customer, identified by `X-Session-Token`. No CSRF check,
  because the token is a header the browser never attaches on its own.
- `GuestAuth`: basket and checkout, open to guests and customers. Guests rely on
  Oscar's signed basket cookie, which a browser sends automatically, so they must pass
  an explicit CSRF check (token from `GET /api/store/csrf`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ninja.security.base import AuthBase
from ninja.utils import check_csrf

from store_backend.api.errors import ForbiddenError
from store_backend.users.middleware import TOKEN_HEADER

if TYPE_CHECKING:
    from django.http import HttpRequest


def _token_user(request: HttpRequest):
    """The user `SessionTokenMiddleware` authenticated from a *valid* token header.

    Not `request.user`: a Django session cookie also sets that, and a cookie is
    exactly what the CSRF check exists to guard.
    """
    if getattr(request, "authenticated_by_session_token", False):
        return request.user
    return None


class CustomerAuth(AuthBase):
    openapi_type = "apiKey"
    openapi_in = "header"
    openapi_name = TOKEN_HEADER

    def __call__(self, request: HttpRequest):
        return _token_user(request)


class GuestAuth(AuthBase):
    openapi_type = "apiKey"
    openapi_in = "header"
    openapi_name = TOKEN_HEADER

    def __call__(self, request: HttpRequest):
        user = _token_user(request)
        if user is not None:
            return user
        if check_csrf(request) is not None:
            msg = "Oturumunuz doğrulanamadı. Sayfayı yenileyip tekrar deneyin."
            raise ForbiddenError(msg, code="csrf_failed")
        return request.user
