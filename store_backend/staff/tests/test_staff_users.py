"""Invite, list and deactivate Staff users; the first Owner command."""

import re
from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.management import CommandError
from django.core.management import call_command
from django.test import Client

from store_backend.conftest import PASSWORD
from store_backend.conftest import make_verified_user
from store_backend.staff.models import AuditEntry
from store_backend.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_api(owner, api_for):
    return api_for(owner, enrol=True)


def invite(api, email="yeni@example.com", name="Yeni Personel"):
    return api.post("/api/admin/staff", {"email": email, "name": name})


def test_invite_creates_an_active_staff_member_in_the_staff_group(owner_api):
    response = invite(owner_api)

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["email"] == "yeni@example.com"
    assert body["role"] == "staff"
    assert body["is_active"] is True
    user = User.objects.get(email="yeni@example.com")
    assert user.is_staff
    assert list(user.groups.values_list("name", flat=True)) == ["Staff"]
    assert not user.has_usable_password()


def test_invite_emails_a_link_to_set_a_password(owner_api):
    invite(owner_api)

    assert [m.to for m in mail.outbox] == [["yeni@example.com"]]
    assert "/hesap/sifre-sifirla/" in mail.outbox[0].body


def test_the_invited_person_can_set_a_password_and_log_in(owner_api):
    invite(owner_api)
    match = re.search(r"/hesap/sifre-sifirla/(\S+)", str(mail.outbox[0].body))
    assert match
    key = match[1]

    reset = Client().post(
        "/_allauth/app/v1/auth/password/reset",
        {"key": key, "password": PASSWORD},
        content_type="application/json",
    )
    login = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": "yeni@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    # Headless answers 401 when the reset does not also log the user in.
    assert reset.status_code == HTTPStatus.UNAUTHORIZED, reset.content
    assert login.status_code == HTTPStatus.OK, login.content


def test_invite_is_audited(owner, owner_api):
    invite(owner_api)

    entry = AuditEntry.objects.get(action="staff_invited")
    assert entry.actor == owner
    assert entry.after
    assert entry.after["email"] == "yeni@example.com"


def test_inviting_an_existing_email_is_a_409(owner_api, staff_user):
    response = invite(owner_api, email=staff_user.email)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "email_taken"


def test_inviting_an_invalid_email_is_a_422(owner_api):
    response = invite(owner_api, email="not-an-email")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "email" in response.json()["fields"]


def test_staff_cannot_invite(staff_user, api_for):
    assert invite(api_for(staff_user)).status_code == HTTPStatus.FORBIDDEN
    assert not User.objects.filter(email="yeni@example.com").exists()


def test_list_staff_is_paginated_and_excludes_customers(
    owner_api,
    staff_user,
    customer,
):
    body = owner_api.get("/api/admin/staff").json()

    emails = {u["email"] for u in body["items"]}
    assert staff_user.email in emails
    assert customer.email not in emails
    assert set(body) == {"items", "total", "page", "page_size"}


def test_deactivate_keeps_the_user_and_audits_it(owner, owner_api, staff_user):
    response = owner_api.post(f"/api/admin/staff/{staff_user.pk}/deactivate")

    assert response.status_code == HTTPStatus.OK
    staff_user.refresh_from_db()
    assert staff_user.is_active is False
    assert AuditEntry.objects.filter(action="staff_deactivated", actor=owner).exists()


def test_a_deactivated_members_existing_token_stops_working(
    owner_api,
    staff_user,
    api_for,
):
    member_api = api_for(staff_user)
    owner_api.post(f"/api/admin/staff/{staff_user.pk}/deactivate")

    assert member_api.get("/api/admin/me").status_code == HTTPStatus.UNAUTHORIZED


def test_a_deactivated_member_cannot_log_in(owner_api, staff_user):
    owner_api.post(f"/api/admin/staff/{staff_user.pk}/deactivate")

    response = Client().post(
        "/_allauth/app/v1/auth/login",
        {"email": staff_user.email, "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code != HTTPStatus.OK


def test_an_owner_cannot_deactivate_themselves(owner, owner_api):
    response = owner_api.post(f"/api/admin/staff/{owner.pk}/deactivate")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "cannot_deactivate_self"


def test_deactivating_an_unknown_or_non_staff_user_is_a_404(owner_api, customer):
    assert (
        owner_api.post(f"/api/admin/staff/{customer.pk}/deactivate").status_code
        == HTTPStatus.NOT_FOUND
    )
    assert (
        owner_api.post("/api/admin/staff/999999/deactivate").status_code
        == HTTPStatus.NOT_FOUND
    )


def test_staff_cannot_deactivate(staff_user, api_for):
    other = make_verified_user(is_staff=True)

    response = api_for(staff_user).post(f"/api/admin/staff/{other.pk}/deactivate")

    assert response.status_code == HTTPStatus.FORBIDDEN


# --- assign_owner -----------------------------------------------------------------


def test_assign_owner_promotes_an_existing_user(customer):
    call_command("assign_owner", customer.email)

    customer.refresh_from_db()
    assert customer.is_staff
    assert customer.groups.filter(name="Owner").exists()


def test_assign_owner_creates_a_missing_user_with_a_verified_email():
    call_command("assign_owner", "sahip@example.com")

    user = User.objects.get(email="sahip@example.com")
    assert user.groups.filter(name="Owner").exists()
    assert EmailAddress.objects.get(user=user).verified


def test_assign_owner_rejects_a_bad_email():
    with pytest.raises(CommandError):
        call_command("assign_owner", "nope")
