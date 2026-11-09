from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.headless.internal.sessionkit import authenticate_by_x_session_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest
    from django.http import HttpResponse

TOKEN_HEADER = "X-Session-Token"  # noqa: S105
ALLAUTH_PREFIX = "/_allauth/"


class SessionTokenMiddleware:
    """Authenticate `X-Session-Token` (allauth app mode) as `request.user`.

    The Start server holds the allauth session token. Ninja's token auth only sets
    `request.auth`, but Oscar's `BasketMiddleware` reads `request.user` to merge a
    guest basket on login and to load the customer's basket. This must run before
    `BasketMiddleware`. allauth's own endpoints resolve the token themselves.

    Sets `request.authenticated_by_session_token` only when the token is valid, so
    callers can tell it apart from a user authenticated by some other means (e.g. a
    Django session cookie) while a bogus token header is present.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.authenticated_by_session_token = False  # type: ignore[attr-defined]
        token = request.headers.get(TOKEN_HEADER)
        if token and not request.path.startswith(ALLAUTH_PREFIX):
            authenticated = authenticate_by_x_session_token(token)
            if authenticated is not None and authenticated[0].is_active:
                request.user = authenticated[0]
                request.authenticated_by_session_token = True  # type: ignore[attr-defined]
        return self.get_response(request)
