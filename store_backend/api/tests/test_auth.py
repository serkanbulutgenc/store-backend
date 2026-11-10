"""Guest/customer auth classes, the CSRF endpoint and the allauth mount."""

from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.test import Client

from store_backend.conftest import LOGIN_URL
from store_backend.conftest import PASSWORD
from store_backend.services import outbox
from store_backend.users.tests.factories import UserFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("store_backend.api.tests.urls"),
]

SIGNUP = "/_allauth/app/v1/auth/signup"


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


# --- CSRF endpoint -----------------------------------------------------------------


def test_csrf_endpoint_returns_a_token_and_an_http_only_cookie(client):
    response = client.get("/api/store/csrf")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["csrf_token"]
    assert response.cookies["csrftoken"]["httponly"]


def test_csrf_endpoint_needs_no_authentication(client):
    assert client.get("/api/store/csrf").status_code == HTTPStatus.OK


# --- Guest auth class (explicit CSRF) ----------------------------------------------


def test_guest_endpoint_rejects_a_missing_csrf_token(csrf_client):
    response = csrf_client.post("/t/api/guest")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "csrf_failed"


def test_guest_endpoint_rejects_a_wrong_csrf_token(csrf_client):
    csrf_client.get("/api/store/csrf")

    response = csrf_client.post("/t/api/guest", headers={"X-CSRFToken": "nope"})

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_guest_endpoint_accepts_a_valid_csrf_token(csrf_client):
    token = csrf_client.get("/api/store/csrf").json()["csrf_token"]

    response = csrf_client.post("/t/api/guest", headers={"X-CSRFToken": token})

    assert response.status_code == HTTPStatus.OK


def test_guest_endpoint_skips_csrf_for_token_authenticated_customers(
    csrf_client,
    customer,
    log_in,
):
    session_token = log_in(csrf_client, customer)

    response = Client(enforce_csrf_checks=True).post(
        "/t/api/guest",
        headers={"X-Session-Token": session_token},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"user": customer.pk}


def test_an_invalid_token_falls_back_to_the_csrf_check(csrf_client):
    response = csrf_client.post("/t/api/guest", headers={"X-Session-Token": "bogus"})

    assert response.status_code == HTTPStatus.FORBIDDEN


# --- Customer auth class (token, no CSRF) ------------------------------------------


def test_customer_endpoint_needs_a_token(csrf_client):
    response = csrf_client.post("/t/api/customer")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == "unauthorized"


def test_customer_endpoint_accepts_a_token_without_csrf(csrf_client, customer, log_in):
    session_token = log_in(csrf_client, customer)

    response = Client(enforce_csrf_checks=True).post(
        "/t/api/customer",
        headers={"X-Session-Token": session_token},
    )

    assert response.status_code == HTTPStatus.OK


# --- allauth headless mount --------------------------------------------------------


def test_allauth_app_mode_is_mounted(client):
    response = client.get("/_allauth/app/v1/config")

    assert response.status_code == HTTPStatus.OK


def test_allauth_browser_mode_is_not_served(client):
    assert client.get("/_allauth/browser/v1/config").status_code == HTTPStatus.NOT_FOUND


def test_signup_requires_email_verification_before_a_session(client):
    response = client.post(
        SIGNUP,
        {"email": "yeni@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    body = response.json()
    assert body["meta"]["is_authenticated"] is False
    pending = [f for f in body["data"]["flows"] if f.get("is_pending")]
    assert [f["id"] for f in pending] == ["verify_email"]
    outbox.dispatch_due()
    assert len(mail.outbox) == 1


def test_unverified_user_cannot_log_in(client):
    user = UserFactory.create(password=PASSWORD)
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        verified=False,
        primary=True,
    )

    response = client.post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["meta"]["is_authenticated"] is False


def test_login_is_rate_limited(client, settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }
    statuses = [
        client.post(
            LOGIN_URL,
            {"email": "x@example.com", "password": "wrong-password-1"},
            content_type="application/json",
        ).status_code
        for _ in range(12)
    ]

    assert HTTPStatus.TOO_MANY_REQUESTS in statuses


def test_signup_is_rate_limited(client):
    statuses = [
        client.post(
            SIGNUP,
            {"email": f"u{i}@example.com", "password": PASSWORD},
            content_type="application/json",
        ).status_code
        for i in range(8)
    ]

    assert HTTPStatus.TOO_MANY_REQUESTS in statuses


def test_password_reset_is_rate_limited(client):
    statuses = [
        client.post(
            "/_allauth/app/v1/auth/password/request",
            {"email": "x@example.com"},
            content_type="application/json",
        ).status_code
        for _ in range(8)
    ]

    assert HTTPStatus.TOO_MANY_REQUESTS in statuses


def test_a_bogus_token_with_a_session_cookie_still_needs_csrf(csrf_client, customer):
    # A valid Django session must not stand in for a valid X-Session-Token.
    csrf_client.force_login(customer)

    response = csrf_client.post("/t/api/guest", headers={"X-Session-Token": "bogus"})

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "csrf_failed"


def test_a_bogus_token_with_a_session_cookie_is_not_a_customer(csrf_client, customer):
    csrf_client.force_login(customer)

    response = csrf_client.post("/t/api/customer", headers={"X-Session-Token": "bogus"})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
