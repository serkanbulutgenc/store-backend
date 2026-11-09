"""Fixed-window rate limits on the Django cache (Redis in production)."""

from __future__ import annotations

from django.core.cache import cache

from store_backend.api.errors import TooManyRequestsError


def hit(scope: str, key: str | None, *, limit: int, window: int) -> None:
    """Count an attempt; raise `TooManyRequestsError` past `limit` per `window` s."""
    cache_key = f"ratelimit:{scope}:{key or 'unknown'}"
    cache.add(cache_key, 0, timeout=window)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.set(cache_key, 1, timeout=window)
        count = 1
    if count > limit:
        raise TooManyRequestsError
