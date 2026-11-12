# ruff: noqa: F811
"""Staff review of return requests: approve or reject, receive, refund."""

from http import HTTPStatus

import pytest
from oscar.apps.order.models import Order

from store_backend.messaging.models import Message
from store_backend.services import audit
from store_backend.services import shop_settings
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnRequest
from store_backend.shop.models import ReturnStatus
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import shop  # noqa: F401
from store_backend.shop.tests.test_checkout import stock
from store_backend.shop.tests.test_returns import deliver
from store_backend.shop.tests.test_returns import link
from store_backend.shop.tests.test_returns import place
from store_backend.shop.tests.test_returns import request_return
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

ORDERS = "/api/admin/orders"
IBAN = "TR330006100519786457841326"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture
def delivered(shop, staff):
    placed = place(shop[1]["M"])
    deliver(staff, placed["number"])
    return placed


def review(api, placed, request_id, step, data=None):
    return api.post(
        f"{ORDERS}/{placed['number']}/returns/{request_id}/{step}",
        data or {},
    )


def filed(placed, **kwargs) -> ReturnRequest:
    response = request_return(placed, **kwargs)
    assert response.status_code == HTTPStatus.CREATED, response.content
    return ReturnRequest.objects.latest("pk")


def mails(template):
    return list(Message.objects.filter(template=template))


def audited(action, placed):
    order = Order.objects.get(number=placed["number"])
    return AuditEntry.objects.filter(action=action, target_id=str(order.pk))


def received(staff, delivered, *, restock=True, reason="wrong_size"):
    request = filed(delivered, quantity=2, reason=reason)
    review(staff, delivered, request.pk, "approve")
    line = request.lines.get().order_line_id
    return request, review(
        staff,
        delivered,
        request.pk,
        "receive",
        {"lines": [{"line_id": line, "restock": restock}]},
    )


# --- request received ----------------------------------------------------------


def test_filing_a_request_emails_the_shopper_and_staff_once_each(
    delivered,
    owner,
):
    shop_settings.update(owner, notification_emails=["a@aura.example"])

    filed(delivered)

    assert [m.recipient for m in mails("return_requested")] == ["ayse@example.com"]
    assert [m.recipient for m in mails("staff_return_request")] == ["a@aura.example"]


# --- approve -------------------------------------------------------------------


def test_approving_is_audited_and_emails_the_shopper_who_pays_shipping(
    delivered,
    staff,
):
    request = filed(delivered, reason="wrong_size")

    response = review(staff, delivered, request.pk, "approve")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["returns"][0]["status"] == "approved"
    assert audited(audit.Action.RETURN_APPROVED, delivered).count() == 1
    [mail] = mails("return_approved")
    assert "siz ödersiniz" in mail.body_text


@pytest.mark.parametrize("reason", ["defective", "wrong_item"])
def test_the_shop_pays_return_shipping_for_its_own_mistakes(
    delivered,
    staff,
    reason,
):
    request = filed(delivered, reason=reason)

    review(staff, delivered, request.pk, "approve")

    [mail] = mails("return_approved")
    assert "mağazamız öder" in mail.body_text


def test_a_request_is_decided_only_once(delivered, staff):
    request = filed(delivered)
    review(staff, delivered, request.pk, "approve")

    again = review(staff, delivered, request.pk, "approve")
    rejecting = review(staff, delivered, request.pk, "reject", {"reason": "Geç"})

    assert again.status_code == HTTPStatus.CONFLICT
    assert again.json()["code"] == "illegal_transition"
    assert rejecting.status_code == HTTPStatus.CONFLICT
    assert len(mails("return_approved")) == 1


def test_a_request_of_another_order_is_not_found(delivered, shop, staff):
    request = filed(delivered)
    other = place(shop[1]["M"])

    response = review(staff, other, request.pk, "approve")

    assert response.status_code == HTTPStatus.NOT_FOUND


# --- reject --------------------------------------------------------------------


def test_rejecting_needs_a_reason_and_tells_the_shopper(delivered, staff):
    request = filed(delivered)

    blank = review(staff, delivered, request.pk, "reject", {"reason": "  "})
    response = review(
        staff,
        delivered,
        request.pk,
        "reject",
        {"reason": "Ürün kullanılmış görünüyor"},
    )

    assert blank.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["returns"][0]["reject_reason"].startswith("Ürün")
    assert audited(audit.Action.RETURN_REJECTED, delivered).count() == 1
    [mail] = mails("return_rejected")
    assert "Ürün kullanılmış görünüyor" in mail.body_text


def test_a_rejected_request_frees_the_quantity_for_a_new_one(delivered, staff):
    request = filed(delivered, quantity=2)
    review(staff, delivered, request.pk, "reject", {"reason": "Olmaz"})

    assert request_return(delivered, quantity=2).status_code == HTTPStatus.CREATED


# --- receive -------------------------------------------------------------------


def test_receiving_with_restock_puts_the_units_back_on_the_shelf(
    delivered,
    shop,
    staff,
):
    on_hand = stock(shop[1]["M"]).num_in_stock

    _, response = received(staff, delivered, restock=True)

    assert response.status_code == HTTPStatus.OK, response.content
    assert stock(shop[1]["M"]).num_in_stock == on_hand + 2
    assert response.json()["returns"][0]["lines"][0]["restocked"] is True
    assert response.json()["status"] == "delivered"  # the Order is untouched
    assert audited(audit.Action.RETURN_RECEIVED, delivered).count() == 1
    assert len(mails("return_received")) == 1


def test_receiving_without_restock_leaves_the_stock_alone(delivered, shop, staff):
    on_hand = stock(shop[1]["M"]).num_in_stock

    request, response = received(staff, delivered, restock=False)

    assert response.status_code == HTTPStatus.OK, response.content
    assert stock(shop[1]["M"]).num_in_stock == on_hand
    assert not ReturnLine.objects.get(request=request).restocked


def test_receiving_needs_a_choice_for_every_line(delivered, shop, staff):
    request = filed(delivered)
    review(staff, delivered, request.pk, "approve")

    response = review(staff, delivered, request.pk, "receive", {"lines": []})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert ReturnRequest.objects.get(pk=request.pk).status == ReturnStatus.APPROVED
    assert mails("return_received") == []


def test_a_request_cannot_be_received_before_it_is_approved(delivered, staff):
    request = filed(delivered)
    line = request.lines.get().order_line_id

    response = review(
        staff,
        delivered,
        request.pk,
        "receive",
        {"lines": [{"line_id": line, "restock": True}]},
    )

    assert response.status_code == HTTPStatus.CONFLICT


# --- refund --------------------------------------------------------------------


def test_the_owner_refunds_a_received_request_and_the_shopper_is_told_once(
    delivered,
    staff,
    boss,
):
    request, _ = received(staff, delivered)

    response = review(
        boss,
        delivered,
        request.pk,
        "refund",
        {"amount": "100.00", "method": "bank_transfer", "iban": IBAN},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    body = response.json()
    assert body["returns"][0]["status"] == "refunded"
    assert body["returns"][0]["refund_amount"] == "100.00"
    assert body["payment_status"] == "refunded"
    assert audited(audit.Action.REFUND, delivered).count() == 1
    assert len(mails("refund_issued")) == 1


def test_staff_cannot_record_the_refund(delivered, staff):
    request, _ = received(staff, delivered)

    response = review(
        staff,
        delivered,
        request.pk,
        "refund",
        {"amount": "100.00", "method": "cash"},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_a_refund_cannot_exceed_what_was_paid_or_repeat(delivered, staff, boss):
    request, _ = received(staff, delivered)

    too_much = review(
        boss,
        delivered,
        request.pk,
        "refund",
        {"amount": "200.01", "method": "cash"},
    )
    ok = review(
        boss,
        delivered,
        request.pk,
        "refund",
        {"amount": "50.00", "method": "cash"},
    )
    again = review(
        boss,
        delivered,
        request.pk,
        "refund",
        {"amount": "50.00", "method": "cash"},
    )

    assert too_much.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert ok.status_code == HTTPStatus.OK, ok.content
    assert again.status_code == HTTPStatus.CONFLICT
    assert len(mails("refund_issued")) == 1


def test_a_request_must_be_received_before_it_is_refunded(delivered, boss):
    request = filed(delivered)

    response = review(
        boss,
        delivered,
        request.pk,
        "refund",
        {"amount": "10.00", "method": "cash"},
    )

    assert response.status_code == HTTPStatus.CONFLICT


def test_the_shopper_sees_the_decision_on_the_tracking_link(delivered, staff):
    request = filed(delivered)
    review(staff, delivered, request.pk, "approve")

    shown = Guest().get(link(delivered)).json()

    assert shown["returns"][0]["status"] == "approved"
