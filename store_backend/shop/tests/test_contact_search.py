"""Contact service: searching and filtering threads (#117)."""

# ruff: noqa: F401, F811

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.cache import cache
from django.utils import timezone

from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactMessageKind
from store_backend.shop.models import ContactReplyState
from store_backend.shop.models import ContactThread
from store_backend.shop.models import ContactThreadStatus
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import staff

pytestmark = pytest.mark.django_db

THREAD_COUNT = 3
SNIPPET_MAX = 140

THREADS = "/api/admin/contact-threads"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


def make(  # noqa: PLR0913
    category,
    name,
    email,
    text,
    *,
    order_number="",
    days_ago=0,
    **extra,
):
    thread = ContactThread.objects.create(
        category=ContactCategory.objects.get(name=category),
        name=name,
        email=email,
        order_number=order_number,
        **extra,
    )
    ContactMessage.objects.create(
        thread=thread,
        kind=ContactMessageKind.SHOPPER,
        body=text,
    )
    stamp = timezone.now() - timedelta(days=days_ago)
    ContactThread.objects.filter(pk=thread.pk).update(last_activity_at=stamp)
    return thread


@pytest.fixture
def seeded():
    return {
        "ayse": make(
            "Order question",
            "Ayşe Yılmaz",
            "ayse@example.com",
            "Siparişim ne zaman gelir? Kargo takip numarası çalışmıyor.",
            order_number="ORD-1001",
            days_ago=1,
        ),
        "mehmet": make(
            "Return",
            "Mehmet Kaya",
            "mehmet@example.org",
            "Ürünü iade etmek istiyorum, beden uymadı.",
            order_number="ORD-1002",
            days_ago=10,
            status=ContactThreadStatus.ANSWERED,
        ),
        "zeynep": make(
            "Complaint",
            "Zeynep Demir",
            "zeynep@example.com",
            "Paketim hasarlı geldi.",
            days_ago=40,
        ),
    }


def ids(api, **params):
    response = api.get(THREADS, data=params)
    assert response.status_code == HTTPStatus.OK, response.content
    return [r["id"] for r in response.json()["items"]]


def test_a_page_of_summaries_latest_activity_first(seeded, staff):
    body = staff.get(THREADS).json()
    assert body["total"] == THREAD_COUNT
    assert [r["id"] for r in body["items"]] == [
        seeded[k].pk for k in ("ayse", "mehmet", "zeynep")
    ]
    first = body["items"][0]
    assert first["category"] == "Order question"
    assert first["snippet"].startswith("Siparişim ne zaman gelir?")
    assert first["pending_reply_count"] == 0


def test_results_paginate(seeded, staff):
    page = staff.get(THREADS, data={"page_size": 2, "page": 2}).json()
    assert page["total"] == THREAD_COUNT
    assert [r["id"] for r in page["items"]] == [seeded["zeynep"].pk]


def test_a_long_message_is_cut_in_the_snippet(staff):
    make("Contact", "Uzun", "uzun@example.com", "söz " * 100)
    snippet = staff.get(THREADS).json()["items"][0]["snippet"]
    assert len(snippet) <= SNIPPET_MAX
    assert snippet.endswith("…")


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("Yılmaz", ["ayse"]),
        ("mehmet@example.org", ["mehmet"]),
        ("example.com", ["ayse", "zeynep"]),
        ("ord-1002", ["mehmet"]),
        ("Complaint", ["zeynep"]),
        ("kargo", ["ayse"]),
        ("iade", ["mehmet"]),
        ("hasarlı", ["zeynep"]),
        ("yokböyle", []),
    ],
)
def test_q_searches_name_email_order_category_and_message(seeded, staff, q, expected):
    assert ids(staff, q=q) == [seeded[k].pk for k in expected]


def test_q_finds_a_word_inside_a_reply_too(seeded, staff):
    ContactMessage.objects.create(
        thread=seeded["zeynep"],
        kind=ContactMessageKind.REPLY,
        body="Değişim için kargo etiketi gönderdik.",
        state=ContactReplyState.SENT,
    )
    assert ids(staff, q="etiketi") == [seeded["zeynep"].pk]


def test_filters_combine(seeded, staff):
    assert ids(staff, status="answered") == [seeded["mehmet"].pk]
    assert ids(staff, q="example", status="new") == [
        seeded["ayse"].pk,
        seeded["zeynep"].pk,
    ]
    category = ContactCategory.objects.get(name="Complaint")
    assert ids(staff, category_id=category.pk, q="example") == [seeded["zeynep"].pk]
    assert ids(staff, category_id=category.pk, status="answered") == []


def test_date_range_bounds_last_activity_both_days_included(seeded, staff):
    today = timezone.localdate()
    assert ids(staff, date_from=(today - timedelta(days=11)).isoformat()) == [
        seeded["ayse"].pk,
        seeded["mehmet"].pk,
    ]
    assert ids(staff, date_to=(today - timedelta(days=10)).isoformat()) == [
        seeded["mehmet"].pk,
        seeded["zeynep"].pk,
    ]
    window = {
        "date_from": (today - timedelta(days=10)).isoformat(),
        "date_to": (today - timedelta(days=10)).isoformat(),
    }
    assert ids(staff, **window) == [seeded["mehmet"].pk]


def test_assignee_filter(seeded, staff_user, boss):
    seeded["ayse"].assignee = staff_user
    seeded["ayse"].save()
    assert ids(boss, assignee_id=staff_user.pk) == [seeded["ayse"].pk]


def test_pending_replies_are_flagged_and_filterable(seeded, staff):
    ContactMessage.objects.create(
        thread=seeded["mehmet"],
        kind=ContactMessageKind.REPLY,
        body="İadeyi başlattık.",
        state=ContactReplyState.PENDING,
    )
    assert ids(staff, has_pending_reply="true") == [seeded["mehmet"].pk]
    assert ids(staff, has_pending_reply="false") == [
        seeded["ayse"].pk,
        seeded["zeynep"].pk,
    ]
    rows = {r["id"]: r for r in staff.get(THREADS).json()["items"]}
    assert rows[seeded["mehmet"].pk]["pending_reply_count"] == 1
    assert rows[seeded["ayse"].pk]["pending_reply_count"] == 0


def test_an_anonymised_thread_is_listed_but_found_only_by_category_status_date(
    seeded,
    staff,
):
    gone = make(
        "Complaint",
        "",
        "",
        "",
        days_ago=3,
        anonymised_at=timezone.now(),
    )
    # What anonymisation leaves behind must not make it a text hit.
    ContactMessage.objects.filter(thread=gone).update(body="Yılmaz example kargo")
    assert gone.pk in ids(staff)
    assert ids(staff, q="Yılmaz") == [seeded["ayse"].pk]
    assert gone.pk not in ids(staff, q="example")
    assert gone.pk not in ids(staff, q="Complaint")
    category = ContactCategory.objects.get(name="Complaint")
    assert gone.pk in ids(staff, category_id=category.pk)
    assert gone.pk in ids(staff, status="new")
    when = (timezone.localdate() - timedelta(days=3)).isoformat()
    assert ids(staff, date_from=when, date_to=when) == [gone.pk]
    row = next(r for r in staff.get(THREADS).json()["items"] if r["id"] == gone.pk)
    assert row["anonymised"] is True
    assert row["snippet"] == ""


def test_unknown_status_and_bad_date_are_rejected(staff):
    assert (
        staff.get(THREADS, data={"status": "bogus"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        staff.get(THREADS, data={"date_from": "yesterday"}).status_code
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
