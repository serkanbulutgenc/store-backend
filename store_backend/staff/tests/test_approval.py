# ruff: noqa: PLR2004, SLF001, F811
"""The shared approval rule and the Owner's queue (phase 4, section 2).

The rule is exercised on a throwaway model with a real table, so no feature has to exist
yet. Collections, which keep their own tables (ADR 0008), are checked through the queue.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db import models

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionChange
from store_backend.catalog.models import CollectionStatus
from store_backend.services import approval
from store_backend.services import approval_queue
from store_backend.staff.models import ApprovalFields
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.models import AuditEntry
from store_backend.staff.models import PendingChange
from store_backend.staff.permissions import Perm
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db


class Probe(ApprovalFields):
    title = models.CharField(max_length=100)
    body = models.TextField(blank=True)

    class Meta:
        app_label = "staff"


def _clean(changes, item):
    out = {k: v for k, v in changes.items() if k in {"title", "body"}}
    title = out.get("title", item.title if item else "")
    if not str(title).strip():
        msg = "Başlık gerekli."
        raise UnprocessableError(msg, fields={"title": [msg]})
    return out


KIND = approval.Kind(
    key="probe",
    label="Deneme",
    model=Probe,
    manage_perm=Perm.MANAGE_COLLECTIONS,
    edit_perm=Perm.EDIT_COLLECTIONS,
    fields=("title", "body"),
    title=lambda item: item.title,
    clean=_clean,
)


@pytest.fixture(scope="module", autouse=True)
def probe_table(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.schema_editor() as editor:
            editor.create_model(Probe)
        approval.register(KIND)
    yield
    approval._KINDS.pop("probe", None)
    # The model has no migration; keep it out of later checks of the app registry.
    apps.all_models["staff"].pop("probe", None)
    apps.clear_cache()
    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.delete_model(Probe)


@pytest.fixture(autouse=True)
def _fresh_content_types():
    # A rolled-back test leaves the throwaway model's ContentType cached with an id
    # that no longer exists.
    ContentType.objects.clear_cache()


@pytest.fixture
def draft(staff_user):
    return approval.create(staff_user, KIND, title="Yaz", body="ilk")


@pytest.fixture
def live(draft, staff_user, owner):
    approval.submit(staff_user, KIND, draft.pk)
    approval.approve(owner, KIND, draft.pk)
    return Probe.objects.get(pk=draft.pk)


def actions():
    return list(AuditEntry.objects.values_list("action", flat=True))


# --- the first publish --------------------------------------------------------------


def test_staff_draft_submit_and_the_owner_approves(draft, staff_user, owner):
    assert draft.status == ApprovalStatus.DRAFT
    approval.submit(staff_user, KIND, draft.pk)
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.PENDING
    approval.approve(owner, KIND, draft.pk)
    done = Probe.objects.get(pk=draft.pk)
    assert done.status == ApprovalStatus.PUBLISHED
    assert done.published_at is not None
    assert [v.version for v in approval.versions(KIND, draft.pk)] == [1]
    assert actions()[:2] == ["approval_approved", "approval_submitted"]


def test_staff_cannot_approve_reject_publish_or_unpublish(draft, staff_user):
    approval.submit(staff_user, KIND, draft.pk)
    for call in (approval.approve, approval.reject, approval.publish):
        with pytest.raises(ForbiddenError):
            call(staff_user, KIND, draft.pk)
    with pytest.raises(ForbiddenError):
        approval.unpublish(staff_user, KIND, draft.pk)


def test_owner_may_publish_a_draft_directly(draft, owner):
    approval.publish(owner, KIND, draft.pk)
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.PUBLISHED
    assert "approval_published" in actions()


def test_rejection_returns_the_draft_with_a_reason_staff_can_read(
    draft,
    staff_user,
    owner,
):
    approval.submit(staff_user, KIND, draft.pk)
    approval.reject(owner, KIND, draft.pk, "  Görsel eksik  ")
    back = Probe.objects.get(pk=draft.pk)
    assert back.status == ApprovalStatus.DRAFT
    assert back.rejection_reason == "Görsel eksik"
    approval.submit(staff_user, KIND, draft.pk)
    assert Probe.objects.get(pk=draft.pk).rejection_reason == ""


def test_a_too_long_reason_is_refused(draft, staff_user, owner):
    approval.submit(staff_user, KIND, draft.pk)
    with pytest.raises(UnprocessableError):
        approval.reject(owner, KIND, draft.pk, "x" * 501)


def test_only_the_submitter_withdraws(draft, staff_user, owner):
    approval.submit(staff_user, KIND, draft.pk)
    with pytest.raises(ForbiddenError):
        approval.withdraw(owner, KIND, draft.pk)
    approval.withdraw(staff_user, KIND, draft.pk)
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.DRAFT


def test_a_pending_item_cannot_be_edited_by_staff(draft, staff_user):
    approval.submit(staff_user, KIND, draft.pk)
    with pytest.raises(ConflictError):
        approval.edit(staff_user, KIND, draft.pk, title="Başka")


def test_staff_edit_a_draft_directly_and_invalid_input_is_refused(draft, staff_user):
    approval.edit(staff_user, KIND, draft.pk, body="yeni")
    assert Probe.objects.get(pk=draft.pk).body == "yeni"
    with pytest.raises(UnprocessableError):
        approval.edit(staff_user, KIND, draft.pk, title=" ")


# --- editing something live ---------------------------------------------------------


def test_a_staff_edit_of_a_live_item_waits_and_the_live_one_stays(live, staff_user):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    assert Probe.objects.get(pk=live.pk).body == "ilk"
    change = PendingChange.objects.get()
    assert change.payload == {"title": "Yaz", "body": "değişti"}
    assert change.submitted_by == staff_user
    assert "approval_change_submitted" in actions()


def test_a_second_staff_edit_amends_the_one_pending_change(live, staff_user):
    approval.edit(staff_user, KIND, live.pk, body="bir")
    approval.edit(staff_user, KIND, live.pk, title="Kış")
    assert PendingChange.objects.count() == 1
    assert PendingChange.objects.get().payload == {"title": "Kış", "body": "bir"}


def test_approving_the_change_applies_it_and_keeps_a_new_version(
    live,
    staff_user,
    owner,
):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    approval.approve(owner, KIND, live.pk)
    assert Probe.objects.get(pk=live.pk).body == "değişti"
    assert not PendingChange.objects.exists()
    assert [v.version for v in approval.versions(KIND, live.pk)] == [2, 1]
    assert approval.versions(KIND, live.pk)[1].payload["body"] == "ilk"
    assert "approval_change_approved" in actions()


def test_rejecting_the_change_drops_it_and_tells_the_submitter(live, staff_user, owner):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    approval.reject(owner, KIND, live.pk, "Olmaz")
    kept = Probe.objects.get(pk=live.pk)
    assert kept.body == "ilk"
    assert kept.status == ApprovalStatus.PUBLISHED
    assert kept.rejection_reason == "Olmaz"
    assert kept.submitted_by == staff_user
    assert not PendingChange.objects.exists()
    assert len(approval.versions(KIND, live.pk)) == 1


def test_the_owner_cannot_edit_or_unpublish_while_a_change_waits(
    live,
    staff_user,
    owner,
):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    with pytest.raises(ConflictError):
        approval.edit(owner, KIND, live.pk, body="sahip")
    with pytest.raises(ConflictError):
        approval.unpublish(owner, KIND, live.pk)


def test_the_owner_edits_a_live_item_directly_when_nothing_waits(live, owner):
    approval.edit(owner, KIND, live.pk, body="sahip")
    assert Probe.objects.get(pk=live.pk).body == "sahip"


def test_the_owner_amends_the_pending_change_before_approving(live, staff_user, owner):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    approval.edit_change(owner, KIND, live.pk, body="düzeltildi")
    approval.approve(owner, KIND, live.pk)
    assert Probe.objects.get(pk=live.pk).body == "düzeltildi"


def test_editing_a_change_that_does_not_exist_is_refused(live, owner):
    with pytest.raises(ConflictError):
        approval.edit_change(owner, KIND, live.pk, body="x")


def test_unpublish_takes_a_live_item_off_and_publish_brings_it_back(live, owner):
    approval.unpublish(owner, KIND, live.pk)
    assert Probe.objects.get(pk=live.pk).status == ApprovalStatus.UNPUBLISHED
    approval.publish(owner, KIND, live.pk)
    assert len(approval.versions(KIND, live.pk)) == 2


def test_staff_cannot_edit_an_unpublished_item(live, staff_user, owner):
    approval.unpublish(owner, KIND, live.pk)
    with pytest.raises(ForbiddenError):
        approval.edit(staff_user, KIND, live.pk, body="x")


# --- the queue ----------------------------------------------------------------------


def test_the_queue_lists_first_drafts_and_changes_oldest_first(
    draft,
    live,
    staff_user,
):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    second = approval.create(staff_user, KIND, title="Kış")
    approval.submit(staff_user, KIND, second.pk)
    rows = approval_queue.items()
    assert [(r["type"], r["title"], r["is_change"]) for r in rows] == [
        ("probe", "Yaz", True),
        ("probe", "Kış", False),
    ]
    assert rows[0]["submitted_by"]["id"] == staff_user.pk
    assert approval_queue.count() == 2


def test_the_diff_for_a_change_shows_live_against_pending(live, staff_user):
    approval.edit(staff_user, KIND, live.pk, body="değişti")
    detail = approval_queue.get("probe", live.pk)
    assert detail["fields"] == [
        {"field": "title", "live": "Yaz", "pending": "Yaz", "changed": False},
        {"field": "body", "live": "ilk", "pending": "değişti", "changed": True},
    ]


def test_the_diff_for_a_first_draft_shows_every_field_as_new(draft, staff_user):
    approval.submit(staff_user, KIND, draft.pk)
    detail = approval_queue.get("probe", draft.pk)
    assert [f["changed"] for f in detail["fields"]] == [True, True]
    assert detail["fields"][0]["live"] is None


def test_an_item_that_is_not_waiting_is_not_found(draft):
    from store_backend.api.errors import NotFoundError  # noqa: PLC0415

    with pytest.raises(NotFoundError):
        approval_queue.get("probe", draft.pk)


def test_the_queue_decides_through_the_shared_rule(draft, staff_user, owner):
    approval.submit(staff_user, KIND, draft.pk)
    approval_queue.approve(owner, "probe", draft.pk)
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.PUBLISHED
    approval_queue.unpublish(owner, "probe", draft.pk)
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.UNPUBLISHED


def test_collections_join_the_queue_and_are_decided_there(staff_user, owner):
    first = Collection.objects.create(
        name="Yeni",
        slug="yeni",
        status=CollectionStatus.PENDING,
        submitted_by=staff_user,
    )
    published = Collection.objects.create(
        name="Eski",
        slug="eski",
        status=CollectionStatus.PUBLISHED,
    )
    CollectionChange.objects.create(
        collection=published,
        name="Eski yeni",
        slug="eski",
        description="",
        product_ids=[],
        submitted_by=staff_user,
    )
    rows = {(r["title"], r["is_change"]) for r in approval_queue.items()}
    assert rows == {("Yeni", False), ("Eski", True)}
    detail = approval_queue.get("collection", published.pk)
    assert {
        "field": "name",
        "live": "Eski",
        "pending": "Eski yeni",
        "changed": True,
    } in (detail["fields"])
    approval_queue.approve(owner, "collection", first.pk)
    approval_queue.reject(owner, "collection", published.pk, "Hayır")
    assert Collection.objects.get(pk=first.pk).status == CollectionStatus.PUBLISHED
    assert Collection.objects.get(pk=published.pk).name == "Eski"
    assert approval_queue.count() == 0


# --- the API ------------------------------------------------------------------------

QUEUE = "/api/admin/approvals"


def test_the_owner_reads_and_decides_the_queue_over_the_api(
    draft,
    staff_user,
    owner,
    api_for,
):
    approval.submit(staff_user, KIND, draft.pk)
    boss = api_for(owner, enrol=True)
    listing = boss.get(QUEUE)
    assert listing.status_code == HTTPStatus.OK
    body = listing.json()
    assert body["count"] == 1
    assert body["items"][0]["title"] == "Yaz"
    detail = boss.get(f"{QUEUE}/probe/{draft.pk}")
    assert detail.status_code == HTTPStatus.OK
    assert detail.json()["fields"][0]["field"] == "title"
    done = boss.post(f"{QUEUE}/probe/{draft.pk}/approve")
    assert done.status_code == HTTPStatus.OK
    assert done.json() == {"count": 0}


def test_rejecting_over_the_api_takes_a_reason(draft, staff_user, owner, api_for):
    approval.submit(staff_user, KIND, draft.pk)
    boss = api_for(owner, enrol=True)
    resp = boss.post(f"{QUEUE}/probe/{draft.pk}/reject", {"reason": "Eksik"})
    assert resp.status_code == HTTPStatus.OK
    assert Probe.objects.get(pk=draft.pk).rejection_reason == "Eksik"


def test_an_unknown_kind_is_not_found(owner, api_for):
    boss = api_for(owner, enrol=True)
    assert boss.post(f"{QUEUE}/nope/1/approve").status_code == HTTPStatus.NOT_FOUND


def test_staff_cannot_see_or_decide_the_queue(draft, staff_user, api_for):
    approval.submit(staff_user, KIND, draft.pk)
    member = api_for(staff_user)
    assert member.get(QUEUE).status_code == HTTPStatus.FORBIDDEN
    assert member.get(f"{QUEUE}/probe/{draft.pk}").status_code == HTTPStatus.FORBIDDEN
    for step in ("approve", "reject", "unpublish"):
        resp = member.post(f"{QUEUE}/probe/{draft.pk}/{step}")
        assert resp.status_code == HTTPStatus.FORBIDDEN
    assert Probe.objects.get(pk=draft.pk).status == ApprovalStatus.PENDING
