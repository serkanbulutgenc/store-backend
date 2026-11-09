"""Who is calling: the client address and agent, for rate limits and consent records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest

MAX_USER_AGENT = 500


def client_ip(request: HttpRequest) -> str | None:
    if getattr(settings, "TRUST_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR") or None


def user_agent(request: HttpRequest) -> str:
    return request.META.get("HTTP_USER_AGENT", "")[:MAX_USER_AGENT]
