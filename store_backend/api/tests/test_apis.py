"""The two mounted APIs and the committed OpenAPI specs."""

from http import HTTPStatus

import pytest
from django.core.management import CommandError
from django.core.management import call_command
from django.urls import reverse

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
