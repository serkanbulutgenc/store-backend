"""Fixed-window rate limits on the Django cache (Redis in production)."""

from __future__ import annotations

from typing import NamedTuple

from django.core.cache import cache

from store_backend.api.errors import TooManyRequestsError


class Rate(NamedTuple):
    limit: int
    window: int  # seconds


def is_limited(scope: str, key: str, rate: Rate) -> bool:
    """Whether `key` has already used up `rate`, without counting this call."""
    return (cache.get(f"ratelimit:{scope}:{key}") or 0) >= rate.limit


def count_attempt(scope: str, key: str | None, rate: Rate) -> None:
    """Count an attempt; raise `TooManyRequestsError` once `rate.limit` is passed."""
    cache_key = f"ratelimit:{scope}:{key or 'unknown'}"
    cache.add(cache_key, 0, timeout=rate.window)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.set(cache_key, 1, timeout=rate.window)
        count = 1
    if count > rate.limit:
        raise TooManyRequestsError
