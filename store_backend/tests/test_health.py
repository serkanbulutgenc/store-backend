from http import HTTPStatus
from unittest import mock

import pytest
from django.db import OperationalError
from django.db.backends.base.base import BaseDatabaseWrapper
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def redis_up():
    with mock.patch("store_backend.health.redis.Redis.from_url") as from_url:
        from_url.return_value.ping.return_value = True
        yield from_url


def test_health_is_200_when_postgres_and_redis_respond(client, redis_up):
    response = client.get(reverse("health"))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok"},
    }


def test_health_is_503_when_redis_is_down(client, settings):
    # A real connection attempt to a closed port, no mocking of the client.
    settings.REDIS_URL = "redis://127.0.0.1:1/0"

    response = client.get(reverse("health"))

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["checks"] == {"postgres": "ok", "redis": "down"}


def test_health_is_503_when_postgres_is_down(client, redis_up):
    # Fail at the connection layer, as a real outage does. This also runs through
    # ATOMIC_REQUESTS handling, which must not turn the outage into a 500.
    with mock.patch.object(
        BaseDatabaseWrapper,
        "ensure_connection",
        side_effect=OperationalError("connection refused"),
    ):
        response = client.get(reverse("health"))

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["checks"] == {"postgres": "down", "redis": "ok"}


def test_health_rejects_non_get(client, redis_up):
    assert client.post(reverse("health")).status_code == HTTPStatus.METHOD_NOT_ALLOWED


def test_health_is_not_cached(client, redis_up):
    response = client.get(reverse("health"))

    assert "no-cache" in response["Cache-Control"]
