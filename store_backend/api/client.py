"""Who is calling: the client address and agent, for rate limits and consent records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from store_backend.shop.models import USER_AGENT_MAX

if TYPE_CHECKING:
    from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str | None:
    if settings.TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR") or None


def user_agent(request: HttpRequest) -> str:
    return request.headers.get("user-agent", "")[:USER_AGENT_MAX]
