"""Returning goods bought with a Voucher: what the Shopper actually paid for them."""

from __future__ import annotations

from decimal import Decimal
from http import HTTPStatus

import pytest
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import Voucher
from oscar.apps.voucher.models import VoucherApplication

from store_backend.catalog.models import VariantProfile
from store_backend.services import audit
from store_backend.services import returns
from store_backend.shop.models import ReturnRequest
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_orders import act
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

D = Decimal
LINK = "/api/store/tracking/link"
VOUCHER = "/api/store/basket/voucher"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


def place_with_voucher(variant, code="YAZ10", quantity=2):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=quantity)
    assert buyer.post(VOUCHER, {"code": code}).status_code == HTTPStatus.OK
    response = buyer.post(CHECKOUT, payload())
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def deliver(staff, placed):
    """Pay what the Order says is due, ship it and mark it delivered."""
    number = placed["number"]
    act(staff, number, "pay", {"amount": placed["total"]})
    act(staff, number, "ship", {"carrier": "Aras", "tracking_number": "1"})
    assert act(staff, number, "deliver").status_code == HTTPStatus.OK


def ask_to_return(placed, quantity=1):
    line = Order.objects.get(number=placed["number"]).lines.get()
    response = Guest().post(
        f"{LINK}/{placed['tracking_token']}/returns",
        {"lines": [{"line_id": line.pk, "quantity": quantity}], "reason": "wrong_size"},
    )
    assert response.status_code == HTTPStatus.CREATED, response.content
    return ReturnRequest.objects.filter(order__number=placed["number"]).latest("pk")


def test_a_returned_unit_is_worth_what_the_shopper_paid_for_it(shop, staff):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    # 2 x 100.00 with 20.00 off: the line is 180.00.
    placed = place_with_voucher(shop[1]["M"])
    deliver(staff, placed)

    first = ask_to_return(placed)
    second = ask_to_return(placed)

    assert returns.refund_due(first) == D("90.00")  # not the 100.00 shelf price
    assert returns.refund_due(second) == D("90.00")


def test_partial_returns_of_one_line_add_up_to_exactly_what_was_paid(shop, staff):
    VariantProfile.objects.filter(product=shop[1]["M"]).update(price_override="33.33")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    # 3 x 33.33 = 99.99; 10.00 off leaves the line at 89.99.
    placed = place_with_voucher(shop[1]["M"], quantity=3)
    deliver(staff, placed)

    due = [returns.refund_due(ask_to_return(placed)) for _ in range(3)]

    # round(89.99 x 1/3) = 30.00, round(x 2/3) = 59.99, round(x 3/3) = 89.99, so each
    # request is worth the step between them. Rounding each unit alone would give 90.00.
    assert due == [D("30.00"), D("29.99"), D("30.00")]
    assert sum(due) == D("89.99")


def test_a_rejected_request_frees_its_units_for_the_next_one(shop, staff):
    VariantProfile.objects.filter(product=shop[1]["M"]).update(price_override="33.33")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"], quantity=3)  # the line is 89.99
    deliver(staff, placed)
    rejected = ask_to_return(placed)
    reason = {"reason": "Kullanılmış"}
    staff.post(
        f"/api/admin/orders/{placed['number']}/returns/{rejected.pk}/reject",
        reason,
    )
    rejected.refresh_from_db()
    assert rejected.status == "rejected"

    first, second = ask_to_return(placed), ask_to_return(placed)

    # Without the rejected one, these are the 1st and 2nd units: 30.00 and 29.99.
    assert (returns.refund_due(first), returns.refund_due(second)) == (
        D("30.00"),
        D("29.99"),
    )


def test_staff_see_what_a_request_is_worth_and_the_shopper_does_not(shop, staff):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])
    deliver(staff, placed)
    ask_to_return(placed)

    detail = staff.get(f"/api/admin/orders/{placed['number']}").json()

    assert detail["returns"][0]["refund_due"] == "90.00"
    shopper = Guest().get(f"{LINK}/{placed['tracking_token']}").json()
    assert "refund_due" not in shopper["returns"][0]


def test_a_rejected_request_is_worth_nothing(shop, staff):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])
    deliver(staff, placed)
    request = ask_to_return(placed)
    staff.post(
        f"/api/admin/orders/{placed['number']}/returns/{request.pk}/reject",
        {"reason": "Kullanılmış"},
    )

    detail = staff.get(f"/api/admin/orders/{placed['number']}").json()

    assert detail["returns"][0]["status"] == "rejected"
    assert detail["returns"][0]["refund_due"] == "0.00"


def test_the_audit_log_keeps_what_was_due_next_to_what_was_refunded(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])  # paid 180.00
    deliver(staff, placed)
    request = ask_to_return(placed)  # one unit: 90.00 is due
    orders = f"/api/admin/orders/{placed['number']}/returns/{request.pk}"
    staff.post(f"{orders}/approve", {})
    line = request.lines.get().order_line_id
    staff.post(f"{orders}/receive", {"lines": [{"line_id": line, "restock": True}]})

    # The Owner may refund more than is due (say, part of the shipping). That is
    # allowed, but the log shows both figures.
    response = boss.post(f"{orders}/refund", {"amount": "100.00", "method": "cash"})

    assert response.status_code == HTTPStatus.OK, response.content
    order = Order.objects.get(number=placed["number"])
    entry = AuditEntry.objects.get(action=audit.Action.REFUND, target_id=str(order.pk))
    assert entry.after["amount"] == "100.00"
    assert entry.after["refund_due"] == "90.00"


def received_and_refunded(placed, staff, boss, amount):
    """File one unit back, approve and receive it, and refund `amount`."""
    request = ask_to_return(placed)
    orders = f"/api/admin/orders/{placed['number']}/returns/{request.pk}"
    staff.post(f"{orders}/approve", {})
    line = request.lines.get().order_line_id
    staff.post(f"{orders}/receive", {"lines": [{"line_id": line, "restock": True}]})
    done = boss.post(f"{orders}/refund", {"amount": amount, "method": "cash"})
    assert done.status_code == HTTPStatus.OK, done.content
    return request


def test_a_return_does_not_give_the_redemption_back(shop, staff, boss):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    voucher.offers.update(max_global_applications=1)
    placed = place_with_voucher(shop[1]["M"])
    deliver(staff, placed)

    received_and_refunded(placed, staff, boss, "90.00")

    # Unlike a cancelled Order, a returned one has used the code.
    assert VoucherApplication.objects.filter(voucher=voucher).count() == 1
    assert Voucher.objects.get(pk=voucher.pk).num_orders == 1


def test_returning_goods_does_not_claw_back_a_voucher_below_its_minimum(
    shop,
    staff,
    boss,
):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10", minimum="150.00")
    placed = place_with_voucher(shop[1]["M"])  # 200.00 qualifies; 20.00 off
    deliver(staff, placed)

    # After the first unit goes back, 100.00 is left: below the 150.00 minimum.
    received_and_refunded(placed, staff, boss, "90.00")
    second = ask_to_return(placed)

    assert returns.refund_due(second) == D("90.00")  # still the discounted price
    order = Order.objects.get(number=placed["number"])
    assert order.discounts.get().amount == D("20.00")
