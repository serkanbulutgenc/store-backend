# ruff: noqa: PLR2004, F811
"""Basket Campaigns: a minimum basket amount, then a TL or percentage off (GLOSSARY:
Basket Campaign, ADR 0011). The guest's basket holds 2 x 100.00 = 200.00."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Line
from oscar.apps.order.models import Order

from store_backend.catalog.models import BasketCampaign
from store_backend.messaging.models import Message
from store_backend.shop.models import OrderBasketCampaign
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_campaign_basket import campaign as item_campaign
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import QUOTE
from store_backend.shop.tests.test_checkout import guest  # noqa: F401
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import quote_body
from store_backend.shop.tests.test_checkout import set_fees
from store_backend.shop.tests.test_checkout import ship_to
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin/basket-campaigns"
BASKET = "/api/store/basket"
VOUCHER = "/api/store/basket/voucher"
D = Decimal


def make(minimum="200", benefit="fixed", value="20", **fields):
    return BasketCampaign.objects.create(
        name=fields.pop("name", f"{benefit} {value}"),
        minimum_amount=D(minimum),
        benefit=benefit,
        value=None if value is None else D(value),
        **fields,
    )


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture(autouse=True)
def flat_fee(owner):
    set_fees(owner, shipping_fee="30.00")


# --- the Owner's management ----------------------------------------------------------


def create(boss, **fields):
    body = {
        "name": "Hafta sonu",
        "minimum_amount": "500",
        "benefit": "fixed",
        "value": "50",
        **fields,
    }
    response = boss.post(ADMIN, body)
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def test_staff_cannot_touch_basket_campaigns(staff_user, api_for, boss):
    made = create(boss)
    staff = api_for(staff_user)
    refused = [
        staff.get(ADMIN),
        staff.post(ADMIN, {}),
        staff.patch(f"{ADMIN}/{made['id']}", {"name": "y"}),
        staff.delete(f"{ADMIN}/{made['id']}"),
    ]
    assert {r.status_code for r in refused} == {HTTPStatus.FORBIDDEN}


def test_the_owner_creates_a_fixed_amount_campaign_with_a_label(boss):
    made = create(boss)

    assert made["label"] == "500 ₺ üzeri 50 ₺ indirim"
    assert (made["minimum_amount"], made["value"]) == ("500.00", "50.00")
    assert (made["is_active"], made["status"], made["orders"]) == (True, "active", 0)
    assert AuditEntry.objects.filter(action="basket_campaign_created").count() == 1


def test_the_label_reads_a_percentage_and_thousands(boss):
    made = create(boss, minimum_amount="1250.50", benefit="percentage", value="10")

    assert made["label"] == "1.250,50 ₺ üzeri %10 indirim"


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"minimum_amount": "0"}, "minimum_amount"),
        ({"minimum_amount": "-5"}, "minimum_amount"),
        ({"value": None}, "value"),
        ({"value": "0"}, "value"),
        ({"value": "500"}, "value"),  # as much as the minimum: the basket could go to 0
        ({"benefit": "percentage", "value": "0"}, "value"),
        ({"benefit": "percentage", "value": "100"}, "value"),
        ({"benefit": "free_shipping", "value": "5"}, "value"),  # takes no amount
        ({"benefit": "nope"}, "benefit"),
    ],
)
def test_a_bad_rule_is_refused(boss, fields, field):
    response = boss.post(
        ADMIN,
        {
            "name": "x",
            "minimum_amount": "500",
            "benefit": "fixed",
            "value": "50",
            **fields,
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not BasketCampaign.objects.exists()


def test_the_end_must_come_after_the_start(boss):
    now = timezone.now()
    response = boss.post(
        ADMIN,
        {
            "name": "x",
            "minimum_amount": "500",
            "benefit": "fixed",
            "value": "50",
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_edit_is_checked_as_a_whole(boss):
    made = create(boss)  # 500 -> 50 off
    item = f"{ADMIN}/{made['id']}"

    ok = boss.patch(item, {"minimum_amount": "300", "value": "30"})
    assert ok.status_code == HTTPStatus.OK
    assert ok.json()["label"] == "300 ₺ üzeri 30 ₺ indirim"

    too_low = boss.patch(item, {"minimum_amount": "20"})  # 30 off a 20 basket
    assert too_low.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert BasketCampaign.objects.get().minimum_amount == D("300.00")
    assert AuditEntry.objects.filter(action="basket_campaign_edited").count() == 1


def test_the_switch_and_the_schedule_decide_the_status(boss):
    made = create(boss)
    item = f"{ADMIN}/{made['id']}"
    assert boss.post(f"{item}/deactivate").json()["status"] == "inactive"
    assert boss.post(f"{item}/activate").json()["status"] == "active"
    future = timezone.now() + timedelta(days=2)
    assert boss.patch(item, {"starts_at": future.isoformat()}).json()["status"] == (
        "scheduled"
    )
    rows = boss.get(ADMIN, data={"status": "scheduled"}).json()["items"]
    assert [r["id"] for r in rows] == [made["id"]]


def test_an_unused_campaign_can_be_deleted(boss):
    made = create(boss)

    assert boss.delete(f"{ADMIN}/{made['id']}").status_code == HTTPStatus.NO_CONTENT
    assert not BasketCampaign.objects.exists()
    assert AuditEntry.objects.filter(action="basket_campaign_deleted").count() == 1


# --- what a basket gets --------------------------------------------------------------


def test_below_the_minimum_nothing_changes(guest):
    make(minimum="200.01")

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["campaigns"] == []
    assert quoted["total"] == "230.00"


def test_exactly_at_the_minimum_the_campaign_applies(guest):
    make(minimum="200", value="20")

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["campaigns"] == [
        {
            "label": "200 ₺ üzeri 20 ₺ indirim",
            "discount": "20.00",
            "free_shipping": False,
        },
    ]
    assert quoted["items_total"] == "200.00"  # before any discount
    assert quoted["total"] == "210.00"  # 200 - 20 + 30 shipping


def test_a_percentage_takes_its_share_of_the_items(guest):
    make(minimum="100", benefit="percentage", value="15")

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["campaigns"][0]["discount"] == "30.00"
    assert quoted["total"] == "200.00"


def test_only_the_campaign_worth_most_applies_and_they_never_stack(guest):
    make(minimum="100", value="20", name="small")
    big = make(minimum="150", benefit="percentage", value="15", name="big")  # 30.00
    make(minimum="300", value="90", name="out of reach")

    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["campaigns"][0]["discount"] == "30.00"
    assert placed["total"] == "200.00"  # 200 - 30 + 30, not also the 20
    assert OrderBasketCampaign.objects.get().campaign_id == big.pk


def test_a_tie_goes_to_the_older_campaign(guest):
    older = make(minimum="100", value="20", name="older")
    make(minimum="100", benefit="percentage", value="10", name="newer")  # also 20.00

    quoted = guest.post(QUOTE, quote_body("")).json()
    guest.post(CHECKOUT, payload())

    assert quoted["campaigns"][0]["discount"] == "20.00"
    assert OrderBasketCampaign.objects.get().campaign_id == older.pk


@pytest.mark.parametrize(
    "fields",
    [
        {"is_active": False},
        {"starts_at": timezone.now() + timedelta(hours=1)},
        {"ends_at": timezone.now() - timedelta(hours=1)},
    ],
)
def test_a_campaign_that_is_off_scheduled_or_ended_does_not_apply(guest, fields):
    make(**fields)

    assert guest.post(QUOTE, quote_body("")).json()["campaigns"] == []


def test_a_fixed_amount_never_takes_the_items_below_zero(guest):
    make(minimum="1", value="0.50")
    BasketCampaign.objects.update(value=D("999.00"))  # past the form's own check

    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["campaigns"][0]["discount"] == "200.00"
    assert placed["total"] == "30.00"  # only shipping is left


# --- stacking with item Campaigns and Vouchers --------------------------------------


def test_the_minimum_is_measured_on_the_campaign_prices(guest, shop):
    product, _ = shop
    item_campaign(20, product)  # the items now cost 160.00
    make(minimum="200")

    assert guest.post(QUOTE, quote_body("")).json()["campaigns"] == []

    BasketCampaign.objects.update(minimum_amount=D("160"))
    quoted = guest.post(QUOTE, quote_body("")).json()
    assert quoted["items_total"] == "160.00"
    assert quoted["campaigns"][0]["discount"] == "20.00"
    assert quoted["total"] == "170.00"  # 160 - 20 + 30


def test_a_voucher_applies_on_top_and_takes_its_share_of_what_is_left(guest):
    make(minimum="200", value="20")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})
    quoted = guest.post(QUOTE, quote_body("")).json()

    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["campaigns"][0]["discount"] == "20.00"
    assert placed["discount"] == "18.00"  # 10% of 200 - 20
    assert placed["total"] == "192.00"  # 200 - 20 - 18 + 30
    assert quoted["total"] == placed["total"]
    lines = Line.objects.filter(order__number=placed["number"])
    assert sum(line.line_price_incl_tax for line in lines) == 162
    assert sum(line.line_price_before_discounts_incl_tax for line in lines) == 200


def test_a_vouchers_minimum_is_measured_before_the_basket_campaign(guest):
    make(minimum="200", value="20")  # leaves 180
    voucher = make_voucher(Benefit.FIXED, "10", code="MIN200", minimum="200")
    response = guest.post(VOUCHER, {"code": voucher.code})

    assert response.status_code == HTTPStatus.OK
    assert response.json()["voucher"]["problem"] is None
    assert guest.post(QUOTE, quote_body("")).json()["voucher_problem"] is None


# --- the basket, the Order and what is frozen ---------------------------------------


def test_the_basket_shows_the_campaign_and_the_vouchers_discount_after_it(guest):
    make(minimum="200", value="20")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})

    basket = guest.get(BASKET).json()

    assert basket["subtotal"] == "200.00"
    assert basket["campaigns"][0]["label"] == "200 ₺ üzeri 20 ₺ indirim"
    assert basket["voucher"]["discount"] == "18.00"


def test_the_order_freezes_the_campaign_and_later_edits_do_not_change_it(
    guest,
    boss,
):
    made = make(minimum="200", value="20")
    placed = guest.post(CHECKOUT, payload()).json()

    made.value = D("50.00")
    made.name = "Renamed"
    made.save()

    row = OrderBasketCampaign.objects.get(order__number=placed["number"])
    assert (row.name, row.label, row.discount) == (
        "fixed 20",
        "200 ₺ üzeri 20 ₺ indirim",
        D("20.00"),
    )
    detail = boss.get(f"/api/admin/orders/{placed['number']}").json()
    assert detail["basket_campaigns"] == [
        {
            "campaign_id": made.pk,
            "name": "fixed 20",
            "label": "200 ₺ üzeri 20 ₺ indirim",
            "discount": "20.00",
            "free_shipping": False,
        },
    ]
    assert detail["total"] == placed["total"]


def test_the_order_total_and_the_lines_add_up_to_what_the_shopper_paid(guest):
    make(minimum="200", benefit="percentage", value="12.5")  # 25.00

    placed = guest.post(CHECKOUT, payload()).json()

    order = Order.objects.get(number=placed["number"])
    assert order.total_incl_tax == D(placed["total"]) == D("205.00")
    lines = Line.objects.filter(order=order)
    assert sum(line.line_price_incl_tax for line in lines) == D("175.00")


def test_a_used_campaign_cannot_be_deleted_but_can_be_switched_off(guest, boss):
    made = make()
    guest.post(CHECKOUT, payload())

    refused = boss.delete(f"{ADMIN}/{made.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "basket_campaign_used"
    assert BasketCampaign.objects.filter(pk=made.pk).exists()
    assert boss.post(f"{ADMIN}/{made.pk}/deactivate").json()["status"] == "inactive"
    assert boss.get(f"{ADMIN}/{made.pk}").json()["orders"] == 1


def test_a_placed_order_without_a_campaign_has_none(guest, boss):
    placed = guest.post(CHECKOUT, payload()).json()

    assert placed["campaigns"] == []
    assert not OrderBasketCampaign.objects.exists()
    detail = boss.get(f"/api/admin/orders/{placed['number']}").json()
    assert detail["basket_campaigns"] == []


def test_the_order_email_names_the_campaign(guest):
    make(minimum="200", value="20")

    placed = guest.post(CHECKOUT, payload()).json()

    message = Message.objects.get(order__number=placed["number"])
    for body in (message.body_text, message.body_html):
        assert "200 ₺ üzeri 20 ₺ indirim" in body
        assert "-₺20,00" in body or "-20,00" in body


def test_order_tracking_shows_the_campaign_the_order_was_placed_under(guest):
    make(minimum="200", value="20")
    placed = guest.post(CHECKOUT, payload()).json()

    tracked = Guest().get(f"/api/store/tracking/link/{placed['tracking_token']}").json()

    assert tracked["items_total"] == "200.00"
    assert tracked["campaigns"][0]["label"] == "200 ₺ üzeri 20 ₺ indirim"
    assert tracked["campaigns"][0]["discount"] == "20.00"
    assert tracked["total"] == placed["total"]


# --- free shipping -------------------------------------------------------------------


def free_shipping(minimum="200", **fields):
    return make(minimum=minimum, benefit="free_shipping", value=None, **fields)


def test_the_owner_creates_a_free_shipping_campaign(boss):
    made = create(boss, benefit="free_shipping", value=None, minimum_amount="750")

    assert made["label"] == "750 ₺ üzeri ücretsiz kargo"
    assert made["value"] is None
    assert made["benefit"] == "free_shipping"


def test_a_free_shipping_campaign_can_be_switched_to_and_from(boss):
    made = create(boss)  # 500 -> 50 off
    item = f"{ADMIN}/{made['id']}"

    to_free = boss.patch(item, {"benefit": "free_shipping", "value": None})
    assert to_free.status_code == HTTPStatus.OK
    assert (to_free.json()["benefit"], to_free.json()["value"]) == (
        "free_shipping",
        None,
    )

    back = boss.patch(item, {"benefit": "fixed"})  # no amount to go with it
    assert back.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_at_the_minimum_shipping_is_free_and_the_items_are_untouched(guest):
    free_shipping("200")

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["campaigns"] == [
        {
            "label": "200 ₺ üzeri ücretsiz kargo",
            "discount": "0.00",
            "free_shipping": True,
        },
    ]
    assert quoted["shipping_fee"] == "0.00"
    assert quoted["total"] == "200.00"  # just the items


def test_below_the_minimum_shipping_is_charged(guest):
    free_shipping("200.01")

    quoted = guest.post(QUOTE, quote_body("")).json()

    assert quoted["campaigns"] == []
    assert quoted["shipping_fee"] == "30.00"


def test_free_shipping_waives_the_shipping_fee_but_not_the_cod_fee(guest, owner):
    set_fees(owner, cod_fee="15.00")
    free_shipping("200")

    placed = guest.post(
        CHECKOUT,
        payload(payment_method="cod", cod_fee_consent=True),
    ).json()

    assert placed["shipping_fee"] == "0.00"
    assert placed["cod_fee"] == "15.00"
    assert placed["total"] == "215.00"
    assert placed["campaigns"][0]["free_shipping"] is True


def test_free_shipping_uses_the_computed_carrier_fee_when_the_province_is_known(
    guest,
    computed_shipping,
):
    free_shipping("200")

    quoted = guest.post(QUOTE, quote_body("TRABZON")).json()
    placed = guest.post(CHECKOUT, ship_to("TRABZON")).json()

    assert quoted["shipping_fee"] == placed["shipping_fee"] == "0.00"
    assert quoted["campaigns"][0]["free_shipping"] is True


def test_a_discount_and_free_shipping_both_apply_when_the_basket_qualifies_for_both(
    guest,
):
    make(minimum="100", value="50", name="50 off")
    free_shipping("100")  # would save the 30.00 shipping

    quoted = guest.post(QUOTE, quote_body("")).json()
    placed = guest.post(CHECKOUT, payload()).json()

    assert [c["label"] for c in placed["campaigns"]] == [
        "100 ₺ üzeri 50 ₺ indirim",  # the discount first
        "100 ₺ üzeri ücretsiz kargo",
    ]
    assert [c["discount"] for c in placed["campaigns"]] == ["50.00", "0.00"]
    assert [c["free_shipping"] for c in placed["campaigns"]] == [False, True]
    assert placed["shipping_fee"] == "0.00"
    assert placed["total"] == "150.00"  # 200 - 50, and no shipping
    assert quoted["campaigns"] == placed["campaigns"]
    assert quoted["total"] == placed["total"]


def test_a_discount_and_free_shipping_each_need_their_own_minimum(guest):
    make(minimum="100", value="50", name="50 off")
    free_shipping("200.01")  # out of reach

    placed = guest.post(CHECKOUT, payload()).json()

    assert [c["free_shipping"] for c in placed["campaigns"]] == [False]
    assert placed["shipping_fee"] == "30.00"
    assert placed["total"] == "180.00"


def test_two_free_shipping_campaigns_apply_as_one(guest):
    older = free_shipping("100", name="older")
    free_shipping("150", name="newer")

    placed = guest.post(CHECKOUT, payload()).json()

    assert len(placed["campaigns"]) == 1
    assert OrderBasketCampaign.objects.get().campaign_id == older.pk


def test_two_discounts_still_do_not_stack_beside_free_shipping(guest):
    make(minimum="100", value="20", name="small")
    big = make(minimum="100", value="60", name="big")
    free_shipping("100")

    placed = guest.post(CHECKOUT, payload()).json()

    assert [c["discount"] for c in placed["campaigns"]] == ["60.00", "0.00"]
    assert placed["total"] == "140.00"
    rows = OrderBasketCampaign.objects.order_by("free_shipping")
    assert next(r.campaign_id for r in rows) == big.pk
    assert rows.count() == 2


def test_the_order_freezes_both_and_the_admin_screen_lists_them(guest, boss):
    make(minimum="100", value="50", name="50 off")
    free_shipping("100")

    placed = guest.post(CHECKOUT, payload()).json()

    rows = OrderBasketCampaign.objects.filter(order__number=placed["number"])
    assert rows.count() == 2
    detail = boss.get(f"/api/admin/orders/{placed['number']}").json()
    assert [c["free_shipping"] for c in detail["basket_campaigns"]] == [False, True]
    assert detail["basket_campaigns"][0]["discount"] == "50.00"


def test_the_email_and_tracking_name_both(guest):
    make(minimum="100", value="50", name="50 off")
    free_shipping("100")

    placed = guest.post(CHECKOUT, payload()).json()

    message = Message.objects.get(order__number=placed["number"])
    for body in (message.body_text, message.body_html):
        assert "100 ₺ üzeri 50 ₺ indirim" in body
        assert "ücretsiz kargo" in body
    tracked = Guest().get(f"/api/store/tracking/link/{placed['tracking_token']}").json()
    assert [c["label"] for c in tracked["campaigns"]] == [
        "100 ₺ üzeri 50 ₺ indirim",
        "100 ₺ üzeri ücretsiz kargo",
    ]


def test_the_basket_lists_every_applied_discount_and_the_voucher_follows(guest):
    make(minimum="100", value="50", name="50 off")
    free_shipping("100")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})

    basket = guest.get(BASKET).json()
    placed = guest.post(CHECKOUT, payload()).json()

    assert basket["subtotal"] == "200.00"
    assert [c["label"] for c in basket["campaigns"]] == [
        "100 ₺ üzeri 50 ₺ indirim",
        "100 ₺ üzeri ücretsiz kargo",
    ]
    assert basket["voucher"]["discount"] == "15.00"  # 10% of 200 - 50
    assert placed["discount"] == "15.00"
    assert placed["total"] == "135.00"  # 200 - 50 - 15, no shipping


def test_free_shipping_that_saves_nothing_is_not_applied(guest, owner):
    set_fees(owner, shipping_fee="0")
    free_shipping("100")

    assert guest.post(QUOTE, quote_body("")).json()["campaigns"] == []


def test_the_order_freezes_free_shipping_and_the_email_says_so(guest, boss):
    made = free_shipping("200")

    placed = guest.post(CHECKOUT, payload()).json()

    row = OrderBasketCampaign.objects.get(order__number=placed["number"])
    assert (row.campaign_id, row.free_shipping, row.discount) == (made.pk, True, 0)
    detail = boss.get(f"/api/admin/orders/{placed['number']}").json()
    assert detail["basket_campaigns"][0]["free_shipping"] is True
    message = Message.objects.get(order__number=placed["number"])
    for body in (message.body_text, message.body_html):
        assert "ücretsiz kargo" in body


def test_the_basket_judges_free_shipping_on_the_flat_fee(guest):
    free_shipping("200")

    basket = guest.get(BASKET).json()

    assert basket["campaigns"] == [
        {
            "label": "200 ₺ üzeri ücretsiz kargo",
            "discount": "0.00",
            "free_shipping": True,
        },
    ]
