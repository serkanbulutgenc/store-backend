"""Contact service: threads, reply drafts, Owner approval, sending (#116)."""

# ruff: noqa: F401, F811

from http import HTTPStatus

import pytest
from django.core.cache import cache

from store_backend.messaging.models import Message
from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactThread
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import staff
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

THREADS = "/api/admin/contact-threads"
REPLIES = "/api/admin/contact-replies"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


@pytest.fixture
def thread():
    category = ContactCategory.objects.get(name="Order question")
    Guest().post(
        "/api/store/contact",
        {
            "category_id": category.pk,
            "name": "Ayşe Yılmaz",
            "email": "ayse@example.com",
            "message": "Siparişim ne zaman gelir?",
        },
    )
    Message.objects.all().delete()  # the acknowledgement is not what is tested here
    return ContactThread.objects.get()


def draft(api, thread, body="Yarın kargoya veriyoruz."):
    return api.post(f"{THREADS}/{thread.pk}/replies", {"body": body})


def mails():
    return list(Message.objects.filter(template="contact_reply"))


# -- reading ----------------------------------------------------------------------


def test_owner_and_staff_both_see_every_thread(thread, staff, boss):
    for api in (staff, boss):
        rows = api.get(THREADS).json()["items"]
        assert [r["id"] for r in rows] == [thread.pk]
        detail = api.get(f"{THREADS}/{thread.pk}").json()
        assert detail["messages"][0]["body"] == "Siparişim ne zaman gelir?"
        assert detail["messages"][0]["kind"] == "shopper"


def test_threads_can_be_filtered_by_status(thread, staff):
    assert staff.get(THREADS, data={"status": "answered"}).json()["items"] == []
    assert (
        staff.get(THREADS, data={"status": "bogus"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


# -- Staff cannot send --------------------------------------------------------------


def test_staff_reply_is_only_a_draft_and_sends_nothing(thread, staff):
    response = draft(staff, thread)

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["state"] == "draft"
    assert mails() == []
    thread.refresh_from_db()
    assert thread.status == "in_progress"


def test_submitting_a_draft_still_sends_nothing(thread, staff):
    reply = draft(staff, thread).json()

    response = staff.post(f"{REPLIES}/{reply['id']}/submit")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["state"] == "pending"
    assert mails() == []


def test_staff_cannot_approve_their_own_draft(thread, staff):
    reply = draft(staff, thread).json()
    staff.post(f"{REPLIES}/{reply['id']}/submit")

    response = staff.post(f"{REPLIES}/{reply['id']}/approve", {})

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert mails() == []
    assert ContactMessage.objects.get(pk=reply["id"]).state == "pending"


def test_staff_cannot_reject_either(thread, staff):
    reply = draft(staff, thread).json()
    staff.post(f"{REPLIES}/{reply['id']}/submit")

    assert (
        staff.post(f"{REPLIES}/{reply['id']}/reject", {}).status_code
        == HTTPStatus.FORBIDDEN
    )


def test_staff_cannot_submit_someone_elses_draft(thread, staff, boss, owner):
    other = ContactMessage.objects.create(
        thread=thread,
        kind="reply",
        body="Başkasının taslağı",
        state="draft",
        author=owner,
    )

    assert (
        staff.post(f"{REPLIES}/{other.pk}/submit").status_code == HTTPStatus.FORBIDDEN
    )
    assert (
        staff.patch(f"{REPLIES}/{other.pk}", {"body": "x"}).status_code
        == HTTPStatus.FORBIDDEN
    )


# -- Owner approves, edits, rejects ---------------------------------------


def waiting(staff, thread, body="Yarın kargoya veriyoruz."):
    reply = draft(staff, thread, body).json()
    staff.post(f"{REPLIES}/{reply['id']}/submit")
    return reply["id"]


def test_owner_approves_as_written_and_it_goes_to_the_outbox(thread, staff, boss):
    reply_id = waiting(staff, thread)

    response = boss.post(f"{REPLIES}/{reply_id}/approve", {})

    assert response.status_code == HTTPStatus.OK
    assert response.json()["state"] == "sent"
    (mail,) = mails()
    assert mail.recipient == "ayse@example.com"
    assert "Yarın kargoya veriyoruz." in mail.body_text
    thread.refresh_from_db()
    assert thread.status == "answered"


def test_owner_edit_is_what_is_sent_and_what_the_thread_shows(thread, staff, boss):
    reply_id = waiting(staff, thread, "Taslak metin")

    response = boss.post(f"{REPLIES}/{reply_id}/approve", {"body": "Sahibin metni"})

    assert response.status_code == HTTPStatus.OK
    (mail,) = mails()
    assert "Sahibin metni" in mail.body_text
    assert "Taslak metin" not in mail.body_text
    shown = [
        m
        for m in boss.get(f"{THREADS}/{thread.pk}").json()["messages"]
        if m["kind"] == "reply"
    ]
    assert [(m["body"], m["drafted_body"], m["state"]) for m in shown] == [
        ("Sahibin metni", "Taslak metin", "sent"),
    ]


def test_an_unchanged_text_keeps_no_separate_draft(thread, staff, boss):
    reply_id = waiting(staff, thread, "Aynı")

    boss.post(f"{REPLIES}/{reply_id}/approve", {"body": "Aynı"})

    assert ContactMessage.objects.get(pk=reply_id).drafted_body == ""


def test_rejection_note_is_visible_to_staff_and_sends_nothing(thread, staff, boss):
    reply_id = waiting(staff, thread)

    response = boss.post(
        f"{REPLIES}/{reply_id}/reject",
        {"note": "Kargo firmasını yazın."},
    )

    assert response.status_code == HTTPStatus.OK
    assert mails() == []
    seen = staff.get(f"{THREADS}/{thread.pk}").json()["messages"][-1]
    assert (seen["state"], seen["rejection_note"]) == (
        "rejected",
        "Kargo firmasını yazın.",
    )


def test_a_rejected_reply_can_be_revised_and_resubmitted(thread, staff, boss):
    reply_id = waiting(staff, thread)
    boss.post(f"{REPLIES}/{reply_id}/reject", {"note": "Kargo firmasını yazın."})

    revised = staff.patch(f"{REPLIES}/{reply_id}", {"body": "Yurtiçi Kargo ile yarın."})
    assert revised.json()["state"] == "draft"
    again = staff.post(f"{REPLIES}/{reply_id}/submit")

    assert again.json()["state"] == "pending"
    assert again.json()["rejection_note"] == ""
    assert boss.post(f"{REPLIES}/{reply_id}/approve", {}).status_code == HTTPStatus.OK
    assert "Yurtiçi Kargo" in mails()[0].body_text


def test_only_a_waiting_reply_can_be_decided(thread, staff, boss):
    reply = draft(staff, thread).json()  # still a draft

    assert (
        boss.post(f"{REPLIES}/{reply['id']}/approve", {}).status_code
        == HTTPStatus.CONFLICT
    )
    assert (
        boss.post(f"{REPLIES}/{reply['id']}/reject", {}).status_code
        == HTTPStatus.CONFLICT
    )
    reply_id = waiting(staff, thread)
    boss.post(f"{REPLIES}/{reply_id}/approve", {})
    assert (
        boss.post(f"{REPLIES}/{reply_id}/approve", {}).status_code
        == HTTPStatus.CONFLICT
    )
    assert len(mails()) == 1


def test_a_sent_reply_cannot_be_edited(thread, staff, boss):
    reply_id = waiting(staff, thread)
    boss.post(f"{REPLIES}/{reply_id}/approve", {})

    assert (
        staff.patch(f"{REPLIES}/{reply_id}", {"body": "x"}).status_code
        == HTTPStatus.CONFLICT
    )


def test_a_waiting_reply_cannot_be_edited_by_staff(thread, staff):
    reply_id = waiting(staff, thread)

    assert (
        staff.patch(f"{REPLIES}/{reply_id}", {"body": "x"}).status_code
        == HTTPStatus.CONFLICT
    )


# -- the Owner replies directly ---------------------------------------


def test_owner_reply_is_sent_at_once_without_approval(thread, boss):
    response = draft(boss, thread, "Merhaba, bugün çıktı.")

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["state"] == "sent"
    (mail,) = mails()
    assert mail.recipient == "ayse@example.com"
    thread.refresh_from_db()
    assert thread.status == "answered"


@pytest.mark.parametrize("body", ["", "   ", "x" * 5001])
def test_an_empty_or_overlong_reply_is_refused(thread, staff, boss, body):
    for api in (staff, boss):
        assert draft(api, thread, body).status_code in (
            HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    assert mails() == []


def test_no_reply_goes_to_an_anonymised_thread(thread, boss, staff):
    from django.utils import timezone  # noqa: PLC0415

    ContactThread.objects.filter(pk=thread.pk).update(
        anonymised_at=timezone.now(),
        email="",
    )

    assert draft(boss, thread).status_code == HTTPStatus.CONFLICT
    assert draft(staff, thread).status_code == HTTPStatus.CONFLICT
    assert mails() == []


# -- status and assignee ------------------------------------------------


def test_status_and_assignee_can_be_set_and_cleared(thread, staff, boss, staff_user):
    response = boss.patch(
        f"{THREADS}/{thread.pk}",
        {"status": "in_progress", "assignee_id": staff_user.pk},
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["assignee"]["id"] == staff_user.pk

    cleared = staff.patch(f"{THREADS}/{thread.pk}", {"assignee_id": None})
    assert cleared.json()["assignee"] is None
    assert cleared.json()["status"] == "in_progress"


def test_a_bad_status_or_assignee_is_refused(thread, staff):
    assert (
        staff.patch(f"{THREADS}/{thread.pk}", {"status": "bogus"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        staff.patch(f"{THREADS}/{thread.pk}", {"assignee_id": 999999}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_decisions_are_audited(thread, staff, boss):
    reply_id = waiting(staff, thread)
    boss.post(f"{REPLIES}/{reply_id}/approve", {})

    actions = set(AuditEntry.objects.values_list("action", flat=True))
    assert {"contact_reply_submitted", "contact_reply_approved"} <= actions
