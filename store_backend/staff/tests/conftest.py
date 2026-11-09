from __future__ import annotations

import pytest
from allauth.mfa.models import Authenticator
from django.contrib.auth.models import Group
from django.test import Client

from store_backend.conftest import make_verified_user


def enrol_totp(user) -> None:
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.TOTP,
        data={"secret": "JBSWY3DPEHPK3PXP"},
    )


def staff_member(role: str, **kwargs):
    user = make_verified_user(is_staff=True, **kwargs)
    user.groups.add(Group.objects.get(name=role))
    return user


@pytest.fixture
def staff_user(db):
    return staff_member("Staff")


@pytest.fixture
def owner(db):
    return staff_member("Owner")


class Api:
    """A test client that sends a user's session token like the Start proxy does."""

    def __init__(self, token: str | None = None) -> None:
        self.client = Client(enforce_csrf_checks=True)
        self.headers = {"X-Session-Token": token} if token else {}

    def get(self, path, **kwargs):
        return self.client.get(path, headers=self.headers, **kwargs)

    def post(self, path, data=None, **kwargs):
        return self.client.post(
            path,
            data or {},
            content_type="application/json",
            headers=self.headers,
            **kwargs,
        )

    def patch(self, path, data=None, **kwargs):
        return self.client.patch(
            path,
            data or {},
            content_type="application/json",
            headers=self.headers,
            **kwargs,
        )


@pytest.fixture
def api_for(log_in):
    """`api_for(user, enrol=True)`: log in, then enrol TOTP if asked (after login)."""

    def _api_for(user, *, enrol: bool = False) -> Api:
        token = log_in(Client(), user)
        if enrol:
            enrol_totp(user)
        return Api(token)

    return _api_for
