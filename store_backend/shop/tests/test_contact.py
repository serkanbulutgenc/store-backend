"""Contact service: categories, the form and its abuse limits (store-infra#115)."""

# ruff: noqa: F401, F811

from http import HTTPStatus

import pytest
from django.core.cache import cache
from oscar.apps.order.models import Order

from store_backend.messaging.models import Message
from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactThread
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import place
from store_backend.shop.tests.test_orders import staff

pytestmark = pytest.mark.django_db

FORM = "/api/store/contact"
CATEGORIES = "/api/store/contact/categories"
ADMIN = "/api/admin/contact-categories"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


def payload(**extra):
    category = ContactCategory.objects.get(name="Order question")
    return {
        "category_id": category.pk,
        "name": "Ayşe Yılmaz",
        "email": "ayse@example.com",
        "message": "Siparişim ne zaman gelir?",
        **extra,
    }


def send(guest=None, **extra):
    return (guest or Guest()).post(FORM, payload(**extra))


def test_seeded_categories():
    names = list(ContactCategory.objects.values_list("name", flat=True))
    assert names == ["Contact", "Request", "Order question", "Return", "Complaint"]


def test_public_list_shows_active_categories_only():
    ContactCategory.objects.filter(name="Request").update(is_active=False)

    names = [c["name"] for c in Guest().get(CATEGORIES).json()]

    assert names == ["Contact", "Order question", "Return", "Complaint"]


def test_guest_submit_stores_new_thread_and_acknowledges():
    response = send()

    assert response.status_code == HTTPStatus.CREATED
    thread = ContactThread.objects.get()
    assert thread.status == "new"
    assert (thread.name, thread.email) == ("Ayşe Yılmaz", "ayse@example.com")
    assert thread.user is None
    assert thread.messages.get().body == "Siparişim ne zaman gelir?"
    mail = Message.objects.get()
    assert mail.recipient == "ayse@example.com"
    assert mail.template == "contact_acknowledgement"


def test_matching_order_number_links_the_thread(shop):
    number = place(shop[1]["M"])

    send(order_number=f" {number.lower()} ")

    thread = ContactThread.objects.get()
    assert thread.order == Order.objects.get(number=number)
    assert thread.order_number == number


@pytest.mark.parametrize("typed", ["AT-ZZZZZZ", "not a number", "12345"])
def test_unmatched_order_number_is_kept_but_links_nothing(typed):
    assert send(order_number=typed).status_code == HTTPStatus.CREATED

    thread = ContactThread.objects.get()
    assert thread.order is None
    assert thread.order_number == typed.upper()


def test_filled_honeypot_looks_like_success_but_stores_nothing():
    response = send(website="http://spam.example")

    assert response.status_code == HTTPStatus.CREATED
    assert not ContactThread.objects.exists()
    assert not ContactMessage.objects.exists()
    assert not Message.objects.exists()


def test_rate_limit_per_ip_stores_nothing_past_the_limit():
    guest = Guest(ip="198.51.100.9")
    for _ in range(5):
        assert send(guest).status_code == HTTPStatus.CREATED

    assert send(guest).status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert ContactThread.objects.count() == 5  # noqa: PLR2004
    assert Message.objects.count() == 5  # noqa: PLR2004
    assert send(Guest(ip="198.51.100.10")).status_code == HTTPStatus.CREATED


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"name": "  "}, "name"),
        ({"email": "not-an-email"}, "email"),
        ({"message": "   "}, "message"),
        ({"message": "x" * 5001}, "message"),
        ({"category_id": 999999}, "category"),
    ],
)
def test_invalid_input_is_refused_and_stores_nothing(extra, field):
    response = send(**extra)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]
    assert not ContactThread.objects.exists()
    assert not Message.objects.exists()


def test_inactive_category_is_refused():
    ContactCategory.objects.filter(name="Order question").update(is_active=False)

    assert send().status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# -- Owner-only category admin ---------------------------------------------------


def test_staff_and_anonymous_cannot_manage_categories(staff, client):
    assert staff.get(ADMIN).status_code == HTTPStatus.FORBIDDEN
    assert staff.post(ADMIN, {"name": "X"}).status_code == HTTPStatus.FORBIDDEN
    assert client.get(ADMIN).status_code == HTTPStatus.UNAUTHORIZED


def test_owner_lists_with_thread_counts(boss):
    send()

    rows = {r["name"]: r for r in boss.get(ADMIN).json()}

    assert rows["Order question"]["thread_count"] == 1
    assert rows["Return"]["thread_count"] == 0


def test_owner_adds_renames_and_deactivates(boss):
    created = boss.post(ADMIN, {"name": "Wholesale"})
    assert created.status_code == HTTPStatus.CREATED
    pk = created.json()["id"]
    assert created.json()["position"] == 5  # noqa: PLR2004

    renamed = boss.patch(f"{ADMIN}/{pk}", {"name": "Toptan"})
    assert renamed.json()["name"] == "Toptan"
    off = boss.patch(f"{ADMIN}/{pk}", {"is_active": False})
    assert off.json()["is_active"] is False
    assert "Toptan" not in [c["name"] for c in Guest().get(CATEGORIES).json()]


@pytest.mark.parametrize("name", ["", "  ", "contact", "x" * 101])
def test_owner_cannot_add_blank_or_repeated_names(boss, name):
    response = boss.post(ADMIN, {"name": name})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unused_category_can_be_deleted(boss):
    pk = boss.post(ADMIN, {"name": "Temp"}).json()["id"]

    assert boss.delete(f"{ADMIN}/{pk}").status_code == HTTPStatus.NO_CONTENT
    assert not ContactCategory.objects.filter(pk=pk).exists()


def test_category_with_threads_cannot_be_deleted(boss):
    send()
    pk = ContactCategory.objects.get(name="Order question").pk

    response = boss.delete(f"{ADMIN}/{pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "contact_category_in_use"
    assert ContactCategory.objects.filter(pk=pk).exists()
