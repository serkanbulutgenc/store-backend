# ruff: noqa: PLR2004
from http import HTTPStatus

import pytest
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from oscar.apps.order.models import Order

from store_backend.conftest import make_verified_user
from store_backend.shop.models import AccountDeletionRequest
from store_backend.shop.models import SavedAddress
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload

pytestmark = pytest.mark.django_db

ACCOUNT = "/api/store/account"
ADDRESSES = f"{ACCOUNT}/addresses"
ORDERS = f"{ACCOUNT}/orders"
DELETIONS = "/api/admin/account-deletions"

ADDRESS = {
    "full_name": "Ayşe Yılmaz",
    "phone": "0532 123 45 67",
    "il": "Mersin",
    "ilce": "Yenişehir",
    "mahalle": "Çiftlikköy Mah.",
    "street": "Gül Sok. No:5",
    "postal_code": "33110",
}


@pytest.fixture
def me(customer, log_in):
    from django.test import Client  # noqa: PLC0415

    return Guest(log_in(Client(), customer))


def place_as_guest(variant, *, email="ayse@example.com"):
    buyer = Guest()
    fill(buyer, variant)
    response = buyer.post(CHECKOUT, payload(email=email))
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()["number"]


def confirm(user, email):
    address = EmailAddress.objects.get(user=user, email=email)
    address.verified = True
    address.save()
    email_confirmed.send(sender=EmailAddress, request=None, email_address=address)


# --- profile ------------------------------------------------------------------


def test_account_needs_a_signed_in_customer():
    assert Guest().get(ACCOUNT).status_code == HTTPStatus.UNAUTHORIZED


def test_profile_is_read_and_updated(me, customer):
    body = me.get(ACCOUNT).json()
    assert body["email"] == customer.email
    assert body["marketing_consent"] is False

    response = me.patch(
        ACCOUNT,
        {"name": "Ayşe  Yılmaz", "phone": "0532 123 45 67", "marketing_consent": True},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["name"] == "Ayşe Yılmaz"
    assert body["phone"] == "+905321234567"
    assert body["marketing_consent"] is True
    customer.refresh_from_db()
    assert customer.marketing_consent_at is not None


def test_marketing_consent_is_withdrawable(me, customer):
    me.patch(ACCOUNT, {"marketing_consent": True})
    me.patch(ACCOUNT, {"marketing_consent": False})

    customer.refresh_from_db()
    assert customer.marketing_consent_at is None


def test_bad_phone_is_rejected(me):
    response = me.patch(ACCOUNT, {"phone": "123"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "phone" in response.json()["fields"]


# --- address book -------------------------------------------------------------


def test_first_address_becomes_the_default(me):
    response = me.post(ADDRESSES, ADDRESS)

    assert response.status_code == HTTPStatus.CREATED, response.content
    assert response.json()["is_default"] is True
    assert response.json()["phone"] == "+905321234567"


def test_a_new_default_replaces_the_old_one(me, customer):
    first = me.post(ADDRESSES, ADDRESS).json()
    second = me.post(ADDRESSES, {**ADDRESS, "is_default": True}).json()

    listing = me.get(ADDRESSES).json()
    assert [a["id"] for a in listing if a["is_default"]] == [second["id"]]
    assert first["id"] in [a["id"] for a in listing]


def test_at_most_five_addresses(me):
    for _ in range(5):
        assert me.post(ADDRESSES, ADDRESS).status_code == HTTPStatus.CREATED

    response = me.post(ADDRESSES, ADDRESS)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "address_limit"


def test_address_needs_every_part(me):
    response = me.post(ADDRESSES, {**ADDRESS, "street": " "})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_deleting_the_default_promotes_the_oldest(me):
    first = me.post(ADDRESSES, ADDRESS).json()
    second = me.post(ADDRESSES, ADDRESS).json()

    assert me.delete(f"{ADDRESSES}/{first['id']}").status_code == 204

    remaining = me.get(ADDRESSES).json()
    assert [(a["id"], a["is_default"]) for a in remaining] == [(second["id"], True)]


def test_an_address_can_be_edited(me):
    saved = me.post(ADDRESSES, ADDRESS).json()

    response = me.put(f"{ADDRESSES}/{saved['id']}", {**ADDRESS, "street": "Yeni Sok."})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["street"] == "Yeni Sok."


def test_addresses_of_others_are_not_found(me):
    other = make_verified_user()
    theirs = SavedAddress.objects.create(
        user=other,
        is_default=True,
        **ADDRESS,
    )

    assert me.put(f"{ADDRESSES}/{theirs.pk}", ADDRESS).status_code == 404
    assert me.delete(f"{ADDRESSES}/{theirs.pk}").status_code == 404
    assert me.get(ADDRESSES).json() == []


# --- orders -------------------------------------------------------------------


def test_history_lists_only_my_orders_and_detail_opens_one(me, customer, shop):
    _, v = shop
    buyer = me
    fill(buyer, v["M"])
    placed = buyer.post(CHECKOUT, payload(email=customer.email)).json()
    place_as_guest(v["M"], email="someone@else.com")

    rows = me.get(ORDERS).json()
    assert [r["number"] for r in rows["items"]] == [placed["number"]]
    assert rows["items"][0]["item_count"] == 2

    detail = me.get(f"{ORDERS}/{placed['number']}")
    assert detail.status_code == HTTPStatus.OK, detail.content
    assert detail.json()["shipping_address"]["il"] == "Mersin"


def test_someone_elses_order_is_not_found(me, shop):
    _, v = shop
    number = place_as_guest(v["M"])

    assert me.get(f"{ORDERS}/{number}").status_code == HTTPStatus.NOT_FOUND


# --- claiming -----------------------------------------------------------------


def test_guest_order_is_attached_after_the_matching_email_is_verified(shop):
    _, v = shop
    number = place_as_guest(v["M"], email="ayse@example.com")
    user = make_verified_user(email="ayse@example.com")
    EmailAddress.objects.filter(user=user).update(verified=False)
    assert Order.objects.get(number=number).user is None

    confirm(user, "ayse@example.com")

    assert Order.objects.get(number=number).user == user


def test_a_new_guest_order_attaches_to_a_verified_account(shop):
    _, v = shop
    user = make_verified_user(email="ayse@example.com")

    number = place_as_guest(v["M"], email="Ayse@Example.com")

    assert Order.objects.get(number=number).user == user


def test_an_owned_order_is_never_reassigned(shop):
    _, v = shop
    number = place_as_guest(v["M"], email="ayse@example.com")
    first = make_verified_user(email="first@example.com")
    Order.objects.filter(number=number).update(user=first)
    second = make_verified_user(email="ayse@example.com")
    EmailAddress.objects.filter(user=second).update(verified=False)

    confirm(second, "ayse@example.com")

    assert Order.objects.get(number=number).user == first


# --- deletion -----------------------------------------------------------------


def test_deletion_request_reaches_the_owner_queue(me, customer, owner, api_for):
    response = me.post(f"{ACCOUNT}/deletion-request")

    assert response.status_code == HTTPStatus.CREATED, response.content
    assert response.json()["deletion_requested"] is True
    queue = api_for(owner, enrol=True).get(DELETIONS).json()
    assert [r["email"] for r in queue["items"]] == [customer.email]


def test_only_one_request_is_open_at_a_time(me):
    me.post(f"{ACCOUNT}/deletion-request")

    response = me.post(f"{ACCOUNT}/deletion-request")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "deletion_pending"


def test_staff_cannot_see_the_queue(staff_user, api_for):
    assert api_for(staff_user).get(DELETIONS).status_code == HTTPStatus.FORBIDDEN


def test_completing_anonymises_but_keeps_orders(me, customer, shop, owner, api_for):
    _, v = shop
    fill(me, v["M"])
    placed = me.post(CHECKOUT, payload(email=customer.email)).json()
    me.post(ADDRESSES, ADDRESS)
    me.post(f"{ACCOUNT}/deletion-request")
    owner_api = api_for(owner, enrol=True)
    request_id = owner_api.get(DELETIONS).json()["items"][0]["id"]

    response = owner_api.post(f"{DELETIONS}/{request_id}/complete")

    assert response.status_code == HTTPStatus.OK, response.content
    customer.refresh_from_db()
    assert customer.is_active is False
    assert customer.email.endswith("@deleted.invalid")
    assert customer.name == ""
    assert not customer.has_usable_password()
    assert not EmailAddress.objects.filter(user=customer).exists()
    assert not SavedAddress.objects.filter(user=customer).exists()
    order = Order.objects.get(number=placed["number"])
    assert order.user == customer
    assert order.billing_address.first_name == "Ayşe"
    assert order.billing_address.line1 == "Gül Sok. No:5 D:3"
    assert AccountDeletionRequest.objects.get(pk=request_id).status == "completed"
    assert owner_api.get(DELETIONS).json()["items"] == []


def test_declining_keeps_the_account(me, customer, owner, api_for):
    me.post(f"{ACCOUNT}/deletion-request")
    owner_api = api_for(owner, enrol=True)
    request_id = owner_api.get(DELETIONS).json()["items"][0]["id"]

    assert owner_api.post(f"{DELETIONS}/{request_id}/decline").status_code == 200

    customer.refresh_from_db()
    assert customer.is_active is True
