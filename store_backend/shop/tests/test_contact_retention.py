"""Contact service: three years, then anonymise; an account deletion at once (#118)."""

# ruff: noqa: F401, F811, PLR2004

from datetime import timedelta

import pytest
from django.utils import timezone

from store_backend.conftest import make_verified_user
from store_backend.services import contact_threads
from store_backend.services import customers
from store_backend.shop.models import AccountDeletionRequest
from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactMessageKind
from store_backend.shop.models import ContactReplyState
from store_backend.shop.models import ContactThread
from store_backend.shop.models import ContactThreadStatus
from store_backend.shop.tasks import purge_contact_threads
from store_backend.shop.tests.test_contact_search import make
from store_backend.staff.tests.conftest import owner

pytestmark = pytest.mark.django_db

NOW = timezone.now()
YEAR = timedelta(days=365)


def aged(thread, age):
    ContactThread.objects.filter(pk=thread.pk).update(last_activity_at=NOW - age)
    thread.refresh_from_db()
    return thread


def thread(age, email="ayse@example.com", **extra):
    return aged(
        make(
            "Order question",
            "Ayşe Yılmaz",
            email,
            "Siparişim ne zaman gelir?",
            order_number="ORD-1001",
            **extra,
        ),
        age,
    )


def reply(found, state, body="Yarın kargoda."):
    return ContactMessage.objects.create(
        thread=found,
        kind=ContactMessageKind.REPLY,
        body=body,
        state=state,
        drafted_body="taslak",
        rejection_note="not",
    )


def test_a_thread_inside_three_years_is_untouched():
    found = thread(3 * YEAR - timedelta(days=2))

    assert contact_threads.purge(NOW) == 0

    found.refresh_from_db()
    assert found.anonymised_at is None
    assert found.email == "ayse@example.com"
    assert found.messages.get().body == "Siparişim ne zaman gelir?"


def test_an_older_thread_loses_its_personal_data_and_keeps_its_counts():
    found = thread(3 * YEAR + timedelta(days=1), status=ContactThreadStatus.ANSWERED)
    sent = reply(found, ContactReplyState.SENT)

    assert contact_threads.purge(NOW) == 1

    found.refresh_from_db()
    assert found.anonymised_at == NOW
    assert (found.name, found.email, found.order_number) == ("", "", "")
    assert found.order is None
    assert found.user is None
    assert found.category.name == "Order question"
    assert found.status == ContactThreadStatus.ANSWERED
    assert found.last_activity_at == NOW - 3 * YEAR - timedelta(days=1)
    sent.refresh_from_db()
    assert sent.body == ""
    assert sent.drafted_body == ""
    assert found.messages.get(kind=ContactMessageKind.SHOPPER).body == ""
    assert found.messages.count() == 2


def test_replies_that_never_went_out_are_deleted():
    found = thread(4 * YEAR)
    for state in (
        ContactReplyState.DRAFT,
        ContactReplyState.PENDING,
        ContactReplyState.REJECTED,
    ):
        reply(found, state)

    contact_threads.purge(NOW)

    assert list(found.messages.values_list("kind", flat=True)) == [
        ContactMessageKind.SHOPPER,
    ]


def test_it_is_idempotent():
    thread(4 * YEAR)

    assert contact_threads.purge(NOW) == 1
    assert contact_threads.purge(NOW + timedelta(days=1)) == 0
    assert ContactThread.objects.get().anonymised_at == NOW


def test_counts_per_category_and_status_are_unchanged():
    thread(4 * YEAR)
    thread(YEAR, email="b@example.com")
    before = sorted(
        ContactThread.objects.values_list("category__name", "status"),
    )

    contact_threads.purge(NOW)

    assert ContactThread.objects.count() == 2
    assert (
        sorted(ContactThread.objects.values_list("category__name", "status")) == before
    )


def test_the_task_runs_the_purge(settings):
    thread(4 * YEAR)

    assert purge_contact_threads() == 1
    assert purge_contact_threads() == 0
    entry = settings.CELERY_BEAT_SCHEDULE["purge-contact-threads"]
    assert entry["task"] == "store_backend.shop.tasks.purge_contact_threads"


# -- account deletion ---------------------------------------------------------------


def test_deleting_an_account_anonymises_that_shoppers_threads_at_once(owner):
    user = make_verified_user(email="ayse@example.com")
    signed_in = thread(timedelta(days=1), email="other@example.com", user=user)
    by_address = thread(timedelta(days=2), email="AYSE@example.com")
    someone_else = thread(timedelta(days=3), email="mehmet@example.com")
    reply(by_address, ContactReplyState.PENDING)
    request = AccountDeletionRequest.objects.create(user=user)

    customers.complete_deletion(owner, request.pk)

    for found in (signed_in, by_address):
        found.refresh_from_db()
        assert found.anonymised_at is not None
        assert found.email == ""
        assert found.messages.count() == 1
        assert found.messages.get().body == ""
    someone_else.refresh_from_db()
    assert someone_else.anonymised_at is None
    assert someone_else.email == "mehmet@example.com"


def test_an_account_with_no_threads_deletes_as_before(owner):
    user = make_verified_user()
    request = AccountDeletionRequest.objects.create(user=user)

    customers.complete_deletion(owner, request.pk)

    user.refresh_from_db()
    assert user.anonymised_at is not None
