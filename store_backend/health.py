"""Liveness/readiness endpoint used by the uptime check and container healthchecks."""

import logging

import redis
from django.conf import settings
from django.db import connection
from django.db import transaction
from django.http import HttpRequest
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2


def _check_postgres() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("Health check: Postgres is unavailable")
        return False
    return True


def _check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=CHECK_TIMEOUT_SECONDS,
            socket_timeout=CHECK_TIMEOUT_SECONDS,
        )
        client.ping()
    except Exception:
        logger.exception("Health check: Redis is unavailable")
        return False
    return True


# ATOMIC_REQUESTS would open a transaction (and so connect to Postgres) before this
# view runs, turning a database outage into a 500 instead of the 503 we report.
@require_GET
@never_cache
@transaction.non_atomic_requests
def health(request: HttpRequest) -> JsonResponse:
    """Return 200 only if both Postgres and Redis respond, otherwise 503."""
    checks = {"postgres": _check_postgres(), "redis": _check_redis()}
    ok = all(checks.values())
    body = {
        "status": "ok" if ok else "unavailable",
        "checks": {name: "ok" if up else "down" for name, up in checks.items()},
    }
    return JsonResponse(body, status=200 if ok else 503)
