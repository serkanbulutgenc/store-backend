"""Permission enforcement and /api/admin/me."""

from http import HTTPStatus

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from ninja import Router
from ninja.testing import TestClient

from store_backend.api.admin import admin_api
from store_backend.api.base import create_api
from store_backend.api.permissions import RequiresPermission
from store_backend.api.permissions import requires
from store_backend.conftest import make_verified_user
from store_backend.staff.permissions import OWNER_ONLY
from store_backend.staff.permissions import Perm
from store_backend.staff.tests.conftest import Api
from store_backend.staff.tests.conftest import enrol_totp

pytestmark = pytest.mark.django_db

OWNER_ONLY_URL = "/api/admin/staff"  # needs Perm.MANAGE_STAFF_USERS


def test_owner_gets_200_and_staff_gets_403_on_an_owner_only_endpoint(
    owner,
    staff_user,
    api_for,
):
    assert api_for(owner, enrol=True).get(OWNER_ONLY_URL).status_code == HTTPStatus.OK

    response = api_for(staff_user).get(OWNER_ONLY_URL)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {
        "code": "forbidden",
        "message": "Bu işlem için yetkiniz yok.",
        "fields": {},
    }


def test_anonymous_gets_401(client):
    assert client.get(OWNER_ONLY_URL).status_code == HTTPStatus.UNAUTHORIZED


def test_a_bogus_token_gets_401():
    assert Api("bogus").get(OWNER_ONLY_URL).status_code == HTTPStatus.UNAUTHORIZED


def test_a_session_cookie_alone_is_not_enough(owner):
    client = Client()
    client.force_login(owner)

    assert client.get("/api/admin/me").status_code == HTTPStatus.UNAUTHORIZED


def test_a_signed_in_customer_is_forbidden(customer, api_for):
    assert api_for(customer).get("/api/admin/me").status_code == HTTPStatus.FORBIDDEN


def test_a_deactivated_staff_member_is_locked_out(staff_user, api_for):
    api = api_for(staff_user)
    staff_user.is_active = False
    staff_user.save()

    assert api.get("/api/admin/me").status_code == HTTPStatus.UNAUTHORIZED


# --- Owner two-factor -------------------------------------------------------------


def test_owner_without_totp_is_blocked_from_the_dashboard_api(owner, api_for):
    response = api_for(owner).get(OWNER_ONLY_URL)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "mfa_enrolment_required"


def test_owner_without_totp_can_still_see_me_to_start_enrolment(owner, api_for):
    body = api_for(owner).get("/api/admin/me").json()

    assert body["role"] == "owner"
    assert body["mfa_required"] is True
    assert body["mfa_enrolled"] is False
    assert body["permissions"] == []


def test_owner_with_totp_gets_every_permission(owner, api_for):
    body = api_for(owner, enrol=True).get("/api/admin/me").json()

    assert body["mfa_enrolled"] is True
    assert body["permissions"] == sorted(p.value for p in Perm)


def test_owner_with_totp_must_pass_the_second_factor_at_login(owner, log_in):
    enrol_totp(owner)

    response = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": owner.email, "password": "correct-horse-battery-staple"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    flows = response.json()["data"]["flows"]
    assert {"id": "mfa_authenticate", "types": ["totp"], "is_pending": True} in flows


def test_staff_does_not_need_totp(staff_user, api_for):
    body = api_for(staff_user).get("/api/admin/me").json()

    assert body["role"] == "staff"
    assert body["mfa_required"] is False
    assert body["permissions"] == sorted(p.value for p in Perm if p not in OWNER_ONLY)


# --- /me --------------------------------------------------------------------------


def test_me_identifies_the_caller(staff_user, api_for):
    body = api_for(staff_user).get("/api/admin/me").json()

    assert body["email"] == staff_user.email
    assert body["id"] == staff_user.pk


def test_me_works_for_a_user_in_no_group(db, api_for):
    user = make_verified_user(is_staff=True)

    body = api_for(user).get("/api/admin/me").json()

    assert body["role"] is None
    assert body["permissions"] == []


# --- Declaring permissions --------------------------------------------------------


def test_every_admin_operation_declares_a_permission():
    undeclared = [
        f"{operation.methods} {path}"
        for _prefix, router in admin_api._routers  # noqa: SLF001
        for path, view in router.path_operations.items()
        for operation in view.operations
        if not any(
            isinstance(callback, RequiresPermission)
            for callback in operation.auth_callbacks
        )
    ]
    assert undeclared == []


def test_an_endpoint_that_forgets_to_declare_a_permission_fails_closed(owner):
    # A new endpoint added without `auth=requires(...)` must not be open to staff.
    api = create_api(title="T", urls_namespace="closed_api", auth=admin_api.auth)
    router = Router()

    @router.get("/x", operation_id="closed_x", response={200: dict})
    def x(request):
        return {}

    api.add_router("/", router)
    request_user = owner

    response = TestClient(api).get("/x", user=request_user)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "permission_not_declared"


def test_requires_returns_a_reusable_declaration():
    declaration = requires(Perm.MARK_PAID)

    assert isinstance(declaration, RequiresPermission)
    assert declaration.permission is Perm.MARK_PAID


def test_groups_are_not_assignable_through_the_api(owner, api_for):
    assert Group.objects.filter(name="Owner").exists()
    assert api_for(owner, enrol=True).post("/api/admin/groups").status_code in {
        HTTPStatus.NOT_FOUND,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }
