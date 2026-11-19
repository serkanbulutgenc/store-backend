# ruff: noqa: PLR2004
"""An Order freezes the Campaign price it was bought at and links to the Campaign
(GLOSSARY: Campaign, ADR 0009): later edits, deactivation or the end of the Campaign
never change it, and refunds give back what was paid."""

from __future__ import annotations

from decimal import Decimal
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order

from store_backend.catalog.models import Campaign
from store_backend.services import returns
from store_backend.shop.models import PreInformationSnapshot
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_campaign_basket import campaign
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_export import fetch
from store_backend.shop.tests.test_export import rows_of
from store_backend.shop.tests.test_orders import ORDERS
from store_backend.shop.tests.test_orders import boss  # noqa: F401
from store_backend.shop.tests.test_orders import staff  # noqa: F401
from store_backend.shop.tests.test_voucher_returns import ask_to_return
from store_backend.shop.tests.test_voucher_returns import deliver

pytestmark = pytest.mark.django_db

D = Decimal
CAMPAIGNS = "/api/admin/campaigns"
VOUCHER = "/api/store/basket/voucher"


def place(variant, *, voucher=None, quantity=2):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=quantity)
    if voucher:
        assert buyer.post(VOUCHER, {"code": voucher}).status_code == HTTPStatus.OK
    response = buyer.post(CHECKOUT, payload())
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def detail(api, number):
    response = api.get(f"{ORDERS}/{number}")
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def test_staff_see_which_campaign_priced_each_line(shop, staff):  # noqa: F811
    product, v = shop
    made = campaign(20, product, name="Kara Cuma")
    placed = place(v["M"])

    line = detail(staff, placed["number"])["lines"][0]

    assert line["unit_price"] == "80.00"
    assert line["campaign"] == {
        "id": made.pk,
        "name": "Kara Cuma",
        "percentage": "20.00",
        "shelf_unit_price": "100.00",
    }


def test_a_line_bought_without_a_campaign_has_none(shop, staff):  # noqa: F811
    placed = place(shop[1]["M"])

    assert detail(staff, placed["number"])["lines"][0]["campaign"] is None


def test_the_shopper_is_not_shown_the_campaign_in_tracking(shop):
    product, v = shop
    campaign(20, product, name="Kara Cuma")
    placed = place(v["M"])

    body = Guest().get(f"/api/store/tracking/link/{placed['tracking_token']}").json()

    assert "Kara Cuma" not in str(body)
    assert "campaign" not in body["lines"][0]


def test_the_pre_information_text_shows_what_the_shopper_saw(shop):
    product, v = shop
    campaign(20, product, name="Kara Cuma")
    placed = place(v["M"])

    text = PreInformationSnapshot.objects.get(order__number=placed["number"]).text

    assert "birim 80,00 TL" in text
    assert "liste fiyatı 100,00 TL" in text
    assert "%20 indirim" in text
    assert "Kara Cuma" not in text  # internal name; the Shopper never sees it


def test_editing_deactivating_or_ending_a_campaign_never_changes_past_orders(
    shop,
    staff,  # noqa: F811
    boss,  # noqa: F811
):
    product, v = shop
    made = campaign(20, product, name="Kara Cuma")
    placed = place(v["M"])
    before = detail(staff, placed["number"])

    assert boss.patch(f"{CAMPAIGNS}/{made.pk}", {"percentage": "50"}).status_code == 200
    assert boss.post(f"{CAMPAIGNS}/{made.pk}/deactivate").status_code == 200
    Campaign.objects.filter(pk=made.pk).update(ends_at=timezone.now())

    after = detail(staff, placed["number"])
    assert after["lines"] == before["lines"]
    assert after["lines"][0]["campaign"]["percentage"] == "20.00"
    assert after["items_total"] == before["items_total"] == "160.00"
    assert after["total"] == before["total"]


def test_a_campaign_an_order_used_can_be_deactivated_but_not_deleted(shop, boss):  # noqa: F811
    product, v = shop
    made = campaign(20, product)
    unused = campaign(10, product, name="Hiç satmadı")
    place(v["M"])

    refused = boss.delete(f"{CAMPAIGNS}/{made.pk}")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "campaign_used"
    assert boss.post(f"{CAMPAIGNS}/{made.pk}/deactivate").status_code == HTTPStatus.OK
    assert boss.delete(f"{CAMPAIGNS}/{unused.pk}").status_code == HTTPStatus.NO_CONTENT


def test_a_return_is_worth_the_campaign_price_net_of_the_voucher(shop, staff):  # noqa: F811
    product, v = shop
    campaign(20, product)  # 2 x 80.00 = 160.00
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")  # 16.00 off: 144.00
    placed = place(v["M"], voucher="YAZ10")
    deliver(staff, placed)

    first = ask_to_return(placed)
    second = ask_to_return(placed)

    assert returns.refund_due(first) == D("72.00")  # not 80.00, not the 100.00 shelf
    assert returns.refund_due(second) == D("72.00")


def test_the_accountant_csv_is_still_the_amounts_paid(shop, boss):  # noqa: F811
    product, v = shop
    campaign(20, product)
    placed = place(v["M"])

    rows = rows_of(fetch(boss))

    assert {r["order_number"] for r in rows} == {placed["number"]}
    product_row = next(r for r in rows if r["line_kind"] == "product")
    assert D(product_row["amount_incl"]) == D("160.00")
    order = Order.objects.get(number=placed["number"])
    assert sum(D(r["amount_incl"]) for r in rows) == order.total_incl_tax
