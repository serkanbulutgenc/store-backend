# ruff: noqa: PLR2004
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.test import Client
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.catalog.models import ProductClassProfile
from store_backend.services import audit
from store_backend.shop.models import OrderRecord
from store_backend.shop.models import ReturnRequest
from store_backend.shop.models import ReturnStatus
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import stock
from store_backend.shop.tests.test_orders import act
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

LINK = "/api/store/tracking/link"
ACCOUNT_ORDERS = "/api/store/account/orders"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


def place(variant, quantity=2, **overrides):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=quantity)
    response = buyer.post(CHECKOUT, payload(**overrides))
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


@pytest.fixture
def placed(shop):
    return place(shop[1]["M"])


def deliver(staff, number, *, days_ago=0):
    act(staff, number, "pay", {"amount": "200.00"})
    act(staff, number, "ship", {"carrier": "X", "tracking_number": "1"})
    response = act(staff, number, "deliver")
    assert response.status_code == HTTPStatus.OK, response.content
    if days_ago:
        OrderRecord.objects.filter(order__number=number).update(
            delivered_at=timezone.now() - timedelta(days=days_ago),
        )


def link(placed, tail=""):
    return f"{LINK}/{placed['tracking_token']}{tail}"


def line_of(placed):
    order = Order.objects.get(number=placed["number"])
    return order.lines.get()


def request_return(placed, quantity=1, reason="wrong_size", **extra):
    body = {
        "lines": [{"line_id": line_of(placed).pk, "quantity": quantity}],
        "reason": reason,
        **extra,
    }
    return Guest().post(link(placed, "/returns"), body)


# --- cancel ------------------------------------------------------------------


def test_a_shopper_cancels_a_new_order_through_the_link_and_stock_comes_back(
    placed,
    shop,
):
    assert stock(shop[1]["M"]).num_allocated == 2

    response = Guest().post(link(placed, "/cancel"))

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "cancelled"
    assert stock(shop[1]["M"]).num_allocated == 0
    assert AuditEntry.objects.filter(
        action=audit.Action.CANCEL,
        target_id=str(Order.objects.get(number=placed["number"]).pk),
        actor__isnull=True,
    ).exists()


def test_a_paid_preparing_order_can_still_be_cancelled_by_the_shopper(
    placed,
    staff,
):
    act(staff, placed["number"], "pay", {"amount": "200.00"})

    response = Guest().post(link(placed, "/cancel"))

    assert response.json()["status"] == "cancelled"
    assert response.json()["payment_status"] == "paid"  # waits for a refund


def test_a_shipped_order_cannot_be_cancelled_by_the_shopper(placed, staff):
    act(staff, placed["number"], "pay", {"amount": "200.00"})
    act(staff, placed["number"], "ship", {"carrier": "X", "tracking_number": "1"})

    response = Guest().post(link(placed, "/cancel"))

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "illegal_transition"


def test_a_bad_link_cannot_cancel(placed):
    response = Guest().post(f"{LINK}/garbage/cancel")

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_a_customer_cancels_their_own_order_but_not_anothers(
    shop,
    customer,
    log_in,
):
    me = Guest(log_in(Client(), customer))
    fill(me, shop[1]["M"])
    mine = me.post(CHECKOUT, payload()).json()
    other = place(shop[1]["M"], email="baska@example.com")

    assert me.post(f"{ACCOUNT_ORDERS}/{other['number']}/cancel").status_code == 404
    response = me.post(f"{ACCOUNT_ORDERS}/{mine['number']}/cancel")
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "cancelled"


# --- return requests ---------------------------------------------------------


def test_a_partial_return_leaves_the_order_delivered_and_is_visible_on_it(
    placed,
    staff,
):
    deliver(staff, placed["number"])

    response = request_return(placed, quantity=1, note="Küçük geldi")

    assert response.status_code == HTTPStatus.CREATED, response.content
    body = response.json()
    assert body["status"] == "delivered"
    [returned] = body["returns"]
    assert returned["status"] == ReturnStatus.REQUESTED
    assert returned["reason"] == "wrong_size"
    assert returned["lines"][0]["quantity"] == 1
    assert body["lines"][0]["returnable_quantity"] == 1
    assert Guest().get(link(placed)).json()["returns"][0]["id"] == returned["id"]


def test_the_rest_of_the_line_can_be_returned_later_but_not_more(placed, staff):
    deliver(staff, placed["number"])
    assert request_return(placed, quantity=1).status_code == HTTPStatus.CREATED
    assert request_return(placed, quantity=1).status_code == HTTPStatus.CREATED

    response = request_return(placed, quantity=1)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "lines" in response.json()["fields"]


def test_more_than_was_delivered_is_refused(placed, staff):
    deliver(staff, placed["number"])

    response = request_return(placed, quantity=3)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert ReturnRequest.objects.count() == 0


def test_a_rejected_request_frees_its_quantity(placed, staff):
    deliver(staff, placed["number"])
    request_return(placed, quantity=2)
    ReturnRequest.objects.update(status=ReturnStatus.REJECTED)

    assert request_return(placed, quantity=2).status_code == HTTPStatus.CREATED


def test_after_day_14_a_return_is_refused(placed, staff):
    deliver(staff, placed["number"], days_ago=15)

    response = request_return(placed)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "return_window_closed"
    assert Guest().get(link(placed)).json()["lines"][0]["returnable_quantity"] == 0


def test_day_14_itself_still_counts(placed, staff):
    deliver(staff, placed["number"], days_ago=13)

    assert request_return(placed).status_code == HTTPStatus.CREATED


def test_a_hygiene_line_cannot_be_returned(placed, staff, shop):
    deliver(staff, placed["number"])
    ProductClassProfile.objects.filter(
        product_class=shop[0].product_class,
    ).update(is_hygiene=True)

    response = request_return(placed)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "hijyen" in response.json()["fields"]["lines"][0].lower()
    assert Guest().get(link(placed)).json()["lines"][0]["returnable_quantity"] == 0


def test_an_order_that_has_not_been_delivered_cannot_be_returned(placed):
    response = request_return(placed)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "return_not_available"


def test_a_return_needs_lines_a_reason_and_a_line_of_this_order(placed, staff, shop):
    deliver(staff, placed["number"])
    other = place(shop[1]["M"], email="baska@example.com")
    foreign = line_of(other).pk

    empty = Guest().post(link(placed, "/returns"), {"lines": [], "reason": "other"})
    bad_reason = request_return(placed, reason="nope")
    foreign_line = Guest().post(
        link(placed, "/returns"),
        {"lines": [{"line_id": foreign, "quantity": 1}], "reason": "other"},
    )
    zero = request_return(placed, quantity=0)

    assert empty.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert bad_reason.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert foreign_line.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert zero.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert ReturnRequest.objects.count() == 0


def test_a_customer_requests_a_return_on_their_own_order(
    shop,
    customer,
    log_in,
    staff,
):
    me = Guest(log_in(Client(), customer))
    fill(me, shop[1]["M"])
    mine = me.post(CHECKOUT, payload()).json()
    deliver(staff, mine["number"])

    response = me.post(
        f"{ACCOUNT_ORDERS}/{mine['number']}/returns",
        {
            "lines": [{"line_id": line_of(mine).pk, "quantity": 1}],
            "reason": "defective",
        },
    )

    assert response.status_code == HTTPStatus.CREATED, response.content
    assert response.json()["returns"][0]["reason"] == "defective"
    detail = me.get(f"{ACCOUNT_ORDERS}/{mine['number']}").json()
    assert len(detail["returns"]) == 1


def test_staff_whole_order_return_flow_is_untouched(placed, staff):
    deliver(staff, placed["number"])
    request_return(placed)

    response = act(staff, placed["number"], "request-return")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "return_requested"
