import pytest
from django.db import DatabaseError
from django.db import connection
from django.db import transaction

from store_backend.services import audit
from store_backend.staff.models import AppendOnlyError
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db


def test_record_writes_an_entry(staff_user):
    entry = audit.record(
        staff_user,
        audit.Action.MARK_PAID,
        target=staff_user,
        summary="Ödeme alındı",
        before={"status": "unpaid"},
        after={"status": "paid"},
    )

    entry.refresh_from_db()
    assert entry.actor == staff_user
    assert entry.actor_email == staff_user.email
    assert entry.action == "mark_paid"
    assert entry.target_type == "users.user"
    assert entry.target_id == str(staff_user.pk)
    assert entry.before == {"status": "unpaid"}
    assert entry.after == {"status": "paid"}
    assert entry.created_at


def test_record_accepts_a_plain_target_and_no_actor():
    entry = audit.record(None, audit.Action.SETTINGS_CHANGE, target=("settings", 1))

    assert (entry.actor, entry.actor_email) == (None, "")
    assert (entry.target_type, entry.target_id) == ("settings", "1")


def test_an_entry_cannot_be_edited_through_the_model(staff_user):
    entry = audit.record(staff_user, audit.Action.LOGIN, target=staff_user)
    entry.summary = "tampered"

    with pytest.raises(AppendOnlyError):
        entry.save()


def test_an_entry_cannot_be_deleted_through_the_model(staff_user):
    entry = audit.record(staff_user, audit.Action.LOGIN, target=staff_user)

    with pytest.raises(AppendOnlyError):
        entry.delete()


def test_querysets_cannot_update_or_delete_entries(staff_user):
    audit.record(staff_user, audit.Action.LOGIN, target=staff_user)

    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.update(summary="tampered")
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.bulk_update([], ["summary"])


@pytest.mark.parametrize(
    "sql",
    ["UPDATE staff_auditentry SET summary = 'x'", "DELETE FROM staff_auditentry"],
)
def test_the_database_refuses_edits_even_to_raw_sql(staff_user, sql):
    audit.record(staff_user, audit.Action.LOGIN, target=staff_user)

    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        connection.cursor().execute(sql)

    assert AuditEntry.objects.count() == 1
