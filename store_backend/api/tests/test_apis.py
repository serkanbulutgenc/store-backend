"""The two mounted APIs and the committed OpenAPI specs."""

from http import HTTPStatus

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError
from django.core.management import call_command
from django.urls import reverse
from ninja import Router
from ninja.testing import TestClient

from store_backend.api.admin import StaffSessionAuth
from store_backend.api.base import create_api

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("namespace", "prefix", "title"),
    [
        ("store_api", "/api/store/", "Aura Tekstil Store API"),
        ("admin_api", "/api/admin/", "Aura Tekstil Admin API"),
    ],
)
def test_each_api_serves_its_own_openapi_document(
    admin_client,
    namespace,
    prefix,
    title,
):
    url = reverse(f"{namespace}:openapi-json")

    response = admin_client.get(url)

    assert url == f"{prefix}openapi.json"
    assert response.status_code == HTTPStatus.OK
    assert response.json()["info"]["title"] == title


def _staff_gated_client():
    api = create_api(title="T", urls_namespace="gate_api", auth=StaffSessionAuth())
    router = Router()

    @router.get("/x", operation_id="gate_x", response={200: dict})
    def x(request):
        return {"ok": True}

    api.add_router("/", router)
    return TestClient(api)


def test_admin_auth_rejects_anonymous_with_the_envelope():
    response = _staff_gated_client().get("/x", user=AnonymousUser())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json() == {
        "code": "unauthorized",
        "message": "Oturum açmanız gerekiyor.",
        "fields": {},
    }


def test_admin_auth_forbids_signed_in_non_staff(user):
    response = _staff_gated_client().get("/x", user=user)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "forbidden"


def test_admin_auth_accepts_active_staff(user):
    user.is_staff = True

    response = _staff_gated_client().get("/x", user=user)

    assert response.status_code == HTTPStatus.OK


def test_committed_specs_match_the_code():
    # Fails when openapi-store.json / openapi-admin.json drift from the code.
    call_command("export_openapi", "--check")


def test_check_fails_when_a_spec_is_stale(tmp_path):
    (tmp_path / "openapi-store.json").write_text("{}\n")
    (tmp_path / "openapi-admin.json").write_text("{}\n")

    with pytest.raises(CommandError, match="stale"):
        call_command("export_openapi", "--check", "--output-dir", str(tmp_path))


def test_export_writes_both_specs_deterministically(tmp_path):
    call_command("export_openapi", "--output-dir", str(tmp_path))
    first = {p.name: p.read_text() for p in tmp_path.iterdir()}
    call_command("export_openapi", "--output-dir", str(tmp_path))

    assert set(first) == {"openapi-store.json", "openapi-admin.json"}
    assert first == {p.name: p.read_text() for p in tmp_path.iterdir()}
    call_command("export_openapi", "--check", "--output-dir", str(tmp_path))
