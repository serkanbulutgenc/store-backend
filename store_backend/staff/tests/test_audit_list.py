"""Owner-only audit log list."""

from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import pytest

from store_backend.services import audit
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

URL = "/api/admin/audit-log"


@pytest.fixture
def entries(owner, staff_user):
    first = audit.record(
        staff_user,
        audit.Action.MARK_PAID,
        target=("order.order", 1),
        summary="Ödeme alındı",
        before={"secret": "x"},
        after={"secret": "y"},
    )
    second = audit.record(owner, audit.Action.SETTINGS_CHANGE, target=("settings", 1))
    third = audit.record(None, audit.Action.CANCEL, target=("order.order", 2))
    return first, second, third


def backdate(entry, when):
    # The table is append-only; bypass the ORM guard the way a fixture must.
    from django.db import connection  # noqa: PLC0415

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE staff_auditentry SET created_at = %s WHERE id = %s",
            [when, entry.pk],
        )
        cursor.execute("SET LOCAL session_replication_role = origin")


def test_staff_get_403_and_owner_gets_200(entries, staff_user, owner, api_for):
    assert api_for(staff_user).get(URL).status_code == HTTPStatus.FORBIDDEN
    assert api_for(owner, enrol=True).get(URL).status_code == HTTPStatus.OK


def test_anonymous_gets_401(client):
    assert client.get(URL).status_code == HTTPStatus.UNAUTHORIZED


def test_rows_expose_what_the_order_detail_does_and_no_before_after(
    entries,
    owner,
    api_for,
):
    items = api_for(owner, enrol=True).get(URL).json()["items"]

    by_action = {e["action"]: e for e in items}
    assert by_action["cancel"]["actor"] == "Sistem"  # system action
    paid = by_action["mark_paid"]
    assert paid["summary"] == "Ödeme alındı"
    assert set(paid) == {
        "id",
        "at",
        "actor",
        "actor_id",
        "action",
        "target_type",
        "target_id",
        "summary",
    }


def test_filter_by_actor_and_action(entries, owner, staff_user, api_for):
    api = api_for(owner, enrol=True)

    by_actor = api.get(f"{URL}?actor={staff_user.pk}").json()["items"]
    by_action = api.get(f"{URL}?action=cancel").json()["items"]

    assert [e["action"] for e in by_actor] == ["mark_paid"]
    assert [e["action"] for e in by_action] == ["cancel"]


def test_unknown_action_is_422(entries, owner, api_for):
    response = api_for(owner, enrol=True).get(f"{URL}?action=nope")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_filter_by_date_range_inclusive(entries, owner, api_for):
    first, second, third = entries
    backdate(first, datetime(2026, 1, 1, 12, tzinfo=UTC))
    backdate(second, datetime(2026, 1, 10, 10, 0, tzinfo=UTC))
    # 22:00 UTC is already the 11th in Mersin (UTC+3)
    backdate(third, datetime(2026, 1, 10, 22, 0, tzinfo=UTC))
    api = api_for(owner, enrol=True)

    window = api.get(f"{URL}?date_from=2026-01-05&date_to=2026-01-10").json()

    assert [e["action"] for e in window["items"]] == ["settings_change"]


def test_paginated(entries, owner, api_for):
    api = api_for(owner, enrol=True)  # signing in wrote a login entry too
    everything = AuditEntry.objects.count()

    body = api.get(f"{URL}?page_size=2&page=2").json()

    assert body["total"] == everything
    assert (body["page"], len(body["items"])) == (2, everything - 2)


def test_detail_has_before_and_after_for_the_owner_only(
    entries,
    owner,
    staff_user,
    api_for,
):
    first = entries[0]

    detail = api_for(owner, enrol=True).get(f"{URL}/{first.pk}")
    refused = api_for(staff_user).get(f"{URL}/{first.pk}")

    assert detail.status_code == HTTPStatus.OK
    assert detail.json()["before"] == {"secret": "x"}
    assert detail.json()["after"] == {"secret": "y"}
    assert detail.json()["summary"] == "Ödeme alındı"
    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_unknown_detail_is_404(owner, api_for):
    response = api_for(owner, enrol=True).get(f"{URL}/999999")

    assert response.status_code == HTTPStatus.NOT_FOUND
