from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.core.cache import cache

from store_backend.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from store_backend.users.models import User


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    # allauth rate limits live in the cache; counters must not leak between tests.
    cache.clear()


@pytest.fixture
def user(db) -> User:
    return UserFactory.create()


PASSWORD = "correct-horse-battery-staple"  # noqa: S105
LOGIN_URL = "/_allauth/app/v1/auth/login"


@pytest.fixture
def customer(db) -> User:
    """A customer with a verified email address."""
    customer = UserFactory.create(password=PASSWORD)
    EmailAddress.objects.create(
        user=customer,
        email=customer.email,
        verified=True,
        primary=True,
    )
    return customer


@pytest.fixture
def log_in():
    """Log in through allauth app mode and return the `X-Session-Token`."""

    def _log_in(client, user) -> str:
        response = client.post(
            LOGIN_URL,
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.OK, response.content
        return response.json()["meta"]["session_token"]

    return _log_in
