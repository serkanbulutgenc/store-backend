# ruff: noqa: PLR2004
from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import pytest
from oscar.apps.order.models import Order

from store_backend.services import tracking
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import stock
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

ORDERS = "/api/admin/orders"
IBAN = "TR330006100519786457841326"  # the standard TR sample, valid check digits


def place(variant, **overrides):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=2)
    response = buyer.post(CHECKOUT, payload(**overrides))
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()["number"]


@pytest.fixture
def transfer(shop):
    return place(shop[1]["M"])


@pytest.fixture
def cod(shop):
    return place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


def act(api, number, action, data=None):
    return api.post(f"{ORDERS}/{number}/{action}", data or {})


def pay(api, number, amount="200.00"):
    return act(api, number, "pay", {"amount": amount})


def order_state(number):
    order = Order.objects.get(number=number)
    return order.status, tracking.payment_status_of(order)


def audit_actions(number):
    order = Order.objects.get(number=number)
    entries = AuditEntry.objects.filter(
        target_type="order.order",
        target_id=str(order.pk),
    ).order_by("id")
    return [e.action for e in entries]


# --- payment -----------------------------------------------------------------


def test_marking_a_transfer_paid_records_amount_and_moves_to_preparing(
    transfer,
    staff,
):
    response = act(
        staff,
        transfer,
        "pay",
        {"amount": "200.00", "paid_at": "2026-11-09T10:30:00+03:00"},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["status"] == "preparing"
    assert body["payment_status"] == "paid"
    assert body["paid_amount"] == "200.00"
    assert datetime.fromisoformat(body["paid_at"]) == datetime(
        2026,
        11,
        9,
        7,
        30,
        tzinfo=UTC,
    )
    assert audit_actions(transfer) == ["mark_paid"]


def test_paying_twice_or_a_cancelled_order_is_a_conflict(transfer, staff):
    assert pay(staff, transfer).status_code == HTTPStatus.OK
    assert pay(staff, transfer).status_code == HTTPStatus.CONFLICT

    other_status = Order.objects.get(number=transfer)
    other_status.status = "cancelled"
    other_status.save()
    assert pay(staff, transfer).status_code == HTTPStatus.CONFLICT


def test_payment_amount_and_date_are_validated(transfer, staff):
    assert pay(staff, transfer, "0").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    future = act(
        staff,
        transfer,
        "pay",
        {"amount": "10", "paid_at": "2099-01-01T00:00:00+03:00"},
    )
    assert future.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert order_state(transfer) == ("new", "unpaid")


# --- cancel ------------------------------------------------------------------


def test_cancel_releases_reserved_stock_and_is_audited(transfer, shop, staff):
    _, v = shop
    assert stock(v["M"]).num_allocated == 2

    response = act(staff, transfer, "cancel", {"reason": "Müşteri vazgeçti"})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancel_reason"] == "Müşteri vazgeçti"
    assert stock(v["M"]).num_allocated == 0
    assert stock(v["M"]).num_in_stock == 5
    assert audit_actions(transfer) == ["cancel"]


def test_cancel_needs_a_reason(transfer, shop, staff):
    response = act(staff, transfer, "cancel", {"reason": "  "})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert stock(shop[1]["M"]).num_allocated == 2


def test_only_the_owner_cancels_a_paid_order(transfer, shop, staff, boss):
    pay(staff, transfer)

    denied = act(staff, transfer, "cancel", {"reason": "Hata"})
    assert denied.status_code == HTTPStatus.FORBIDDEN
    assert order_state(transfer) == ("preparing", "paid")
    assert stock(shop[1]["M"]).num_allocated == 2

    allowed = act(boss, transfer, "cancel", {"reason": "Stok hatası"})
    assert allowed.status_code == HTTPStatus.OK
    assert order_state(transfer) == ("cancelled", "paid")
    assert stock(shop[1]["M"]).num_allocated == 0


# --- ship, deliver, COD ------------------------------------------------------


def test_ship_consumes_stock_and_shows_on_tracking(cod, shop, staff):
    response = act(
        staff,
        cod,
        "ship",
        {"carrier": "Yurtiçi Kargo", "tracking_number": "YK123"},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["status"] == "shipped"
    assert (body["carrier"], body["tracking_number"]) == ("Yurtiçi Kargo", "YK123")
    record = stock(shop[1]["M"])
    assert (record.num_allocated, record.num_in_stock) == (0, 3)
    assert audit_actions(cod) == ["ship"]

    public = (
        Guest()
        .post(
            "/api/store/tracking",
            {"number": cod, "contact": "ayse@example.com"},
        )
        .json()
    )
    assert public["carrier"] == "Yurtiçi Kargo"
    assert public["tracking_number"] == "YK123"


def test_ship_needs_carrier_and_tracking_number(cod, staff):
    response = act(staff, cod, "ship", {"carrier": " ", "tracking_number": ""})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert set(response.json()["fields"]) == {"carrier", "tracking_number"}
    assert order_state(cod)[0] == "preparing"


def test_an_unpaid_transfer_cannot_ship(transfer, shop, staff):
    response = act(staff, transfer, "ship", {"carrier": "X", "tracking_number": "1"})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "illegal_transition"
    assert stock(shop[1]["M"]).num_allocated == 2


def test_cod_becomes_paid_on_delivery(cod, staff):
    act(staff, cod, "ship", {"carrier": "X", "tracking_number": "1"})
    assert order_state(cod) == ("shipped", "unpaid")

    response = act(staff, cod, "deliver")

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert (body["status"], body["payment_status"]) == ("delivered", "paid")
    assert body["paid_amount"] == body["total"]
    assert audit_actions(cod) == ["ship", "deliver"]


# --- illegal transitions -----------------------------------------------------


@pytest.mark.parametrize("action", ["deliver", "request-return", "mark-returned"])
def test_transitions_out_of_order_return_409(transfer, staff, action):
    response = act(staff, transfer, action)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "illegal_transition"
    assert order_state(transfer) == ("new", "unpaid")
    assert audit_actions(transfer) == []


@pytest.mark.parametrize(
    ("action", "named"),
    [
        ("deliver", "deliver"),
        ("request-return", "return_request"),
        ("mark-returned", "return_received"),
    ],
)
def test_an_illegal_transition_names_the_action_it_refused(
    transfer,
    staff,
    action,
    named,
):
    response = act(staff, transfer, action)

    assert response.json()["fields"] == {"status": [named]}


def test_a_shipped_or_cancelled_order_cannot_be_cancelled(cod, transfer, staff):
    act(staff, cod, "ship", {"carrier": "X", "tracking_number": "1"})
    act(staff, transfer, "cancel", {"reason": "Vazgeçti"})

    for number in (cod, transfer):
        response = act(staff, number, "cancel", {"reason": "Tekrar"})
        assert response.status_code == HTTPStatus.CONFLICT
    assert audit_actions(transfer) == ["cancel"]


def test_unknown_order_is_404(staff):
    assert pay(staff, "AT-NOPE22").status_code == HTTPStatus.NOT_FOUND


# --- returns and refunds -----------------------------------------------------


def returned_cod(staff, number):
    act(staff, number, "ship", {"carrier": "X", "tracking_number": "1"})
    act(staff, number, "deliver")
    assert act(staff, number, "request-return").status_code == HTTPStatus.OK
    assert act(staff, number, "mark-returned").status_code == HTTPStatus.OK


def test_return_then_refund_is_owner_only_and_audited(cod, staff, boss):
    returned_cod(staff, cod)
    assert order_state(cod) == ("returned", "paid")
    body = {"amount": "200.00", "method": "bank_transfer", "iban": IBAN}

    assert act(staff, cod, "refund", body).status_code == HTTPStatus.FORBIDDEN

    response = act(
        boss,
        cod,
        "refund",
        {**body, "iban": "tr33 0006 1005 1978 6457 8413 26"},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["refund_iban"] == IBAN
    assert order_state(cod) == ("returned", "refunded")
    assert audit_actions(cod) == [
        "ship",
        "deliver",
        "return_request",
        "return_received",
        "refund",
    ]
    assert act(boss, cod, "refund", body).status_code == HTTPStatus.CONFLICT


@pytest.mark.parametrize(
    "body",
    [
        {"amount": "200.00", "method": "bank_transfer", "iban": ""},
        {
            "amount": "200.00",
            "method": "bank_transfer",
            "iban": "TR000000000000000000000000",
        },
        {"amount": "999.00", "method": "cash"},
    ],
)
def test_refund_validates_iban_and_amount(cod, staff, boss, body):
    returned_cod(staff, cod)

    response = act(boss, cod, "refund", body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert order_state(cod) == ("returned", "paid")


def test_a_cash_refund_needs_no_iban(cod, staff, boss):
    returned_cod(staff, cod)

    response = act(boss, cod, "refund", {"amount": "100.00", "method": "cash"})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["refund_iban"] == ""


def test_a_cancelled_paid_order_can_be_refunded_but_an_unpaid_one_cannot(
    transfer,
    cod,
    staff,
    boss,
):
    pay(staff, transfer)
    act(boss, transfer, "cancel", {"reason": "Stok yok"})
    act(boss, cod, "cancel", {"reason": "Vazgeçti"})
    body = {"amount": "200.00", "method": "cash"}

    assert act(boss, transfer, "refund", body).status_code == HTTPStatus.OK
    assert act(boss, cod, "refund", body).status_code == HTTPStatus.CONFLICT


# --- edits -------------------------------------------------------------------


def test_shipping_address_and_phone_edit_while_new_or_preparing(cod, staff):
    response = staff.patch(
        f"{ORDERS}/{cod}/shipping",
        {"street": "Yeni Sok. No:9", "phone": "0533 111 22 33"},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    address = response.json()["shipping_address"]
    assert address["street"] == "Yeni Sok. No:9"
    assert address["phone"] == "+905331112233"
    assert address["il"] == "Mersin"
    entry = AuditEntry.objects.get(action="order_edit")
    assert entry.before
    assert entry.after
    assert entry.before["street"] == "Gül Sok. No:5 D:3"
    assert entry.after["street"] == "Yeni Sok. No:9"


def test_shipping_edit_is_refused_once_shipped_or_for_a_bad_phone(cod, staff):
    bad = staff.patch(f"{ORDERS}/{cod}/shipping", {"phone": "123"})
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    act(staff, cod, "ship", {"carrier": "X", "tracking_number": "1"})
    late = staff.patch(f"{ORDERS}/{cod}/shipping", {"street": "Başka Sok."})
    assert late.status_code == HTTPStatus.CONFLICT
    assert late.json()["code"] == "illegal_transition"


def test_email_correction_is_audited_and_changes_the_lookup(transfer, staff):
    response = staff.patch(
        f"{ORDERS}/{transfer}/email",
        {"email": " Ayse@Example.org "},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["email"] == "ayse@example.org"
    found = Guest(ip="203.0.113.60").post(
        "/api/store/tracking",
        {"number": transfer, "contact": "ayse@example.org"},
    )
    assert found.status_code == HTTPStatus.OK
    assert AuditEntry.objects.get(action="order_edit").before == {
        "email": "ayse@example.com",
    }
    bad = staff.patch(f"{ORDERS}/{transfer}/email", {"email": "nope"})
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_invoice_details_work_in_any_status_and_are_validated(cod, staff):
    act(staff, cod, "ship", {"carrier": "X", "tracking_number": "1"})
    url = f"{ORDERS}/{cod}/invoice-details"

    bad = staff.patch(url, {"buyer_tax_number": "12ab"})
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    response = staff.patch(
        url,
        {"buyer_tax_number": "1234567890", "company_title": "Örnek  Tekstil Ltd."},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["buyer_tax_number"] == "1234567890"
    assert response.json()["company_title"] == "Örnek Tekstil Ltd."
    assert AuditEntry.objects.filter(action="order_edit").count() == 1


# --- reading -----------------------------------------------------------------


def test_list_filters_by_status_payment_and_text(transfer, cod, staff):
    pay(staff, transfer)

    everything = staff.get(ORDERS).json()
    assert everything["total"] == 2
    assert {row["number"] for row in everything["items"]} == {transfer, cod}

    paid = staff.get(f"{ORDERS}?payment_status=paid").json()
    assert [r["number"] for r in paid["items"]] == [transfer]
    unpaid = staff.get(f"{ORDERS}?payment_status=unpaid").json()
    assert [r["number"] for r in unpaid["items"]] == [cod]
    new = staff.get(f"{ORDERS}?status=new").json()
    assert new["total"] == 0
    by_text = staff.get(f"{ORDERS}?q={cod.lower()}").json()
    assert [r["number"] for r in by_text["items"]] == [cod]
    by_name = staff.get(f"{ORDERS}?q=yılmaz").json()
    assert by_name["total"] == 2
    assert staff.get(f"{ORDERS}?ordering=bogus").status_code == 422


def test_detail_lists_lines_actions_and_audit(transfer, staff):
    pay(staff, transfer)

    body = staff.get(f"{ORDERS}/{transfer}").json()

    assert body["lines"][0]["quantity"] == 2
    assert body["payment_method"] == "bank_transfer"
    assert body["customer_id"] is None
    assert set(body["actions"]) == {"cancel", "edit_shipping", "ship"}
    assert [a["action"] for a in body["audit"]] == ["mark_paid"]


def test_order_endpoints_require_a_signed_in_member_of_staff(transfer):
    anonymous = Guest()

    assert anonymous.get(ORDERS).status_code == HTTPStatus.UNAUTHORIZED
