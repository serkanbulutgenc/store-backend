from datetime import timedelta

import pytest
from django.utils import timezone
from oscar.apps.order.models import Order
from oscar.apps.partner.models import StockRecord

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.messaging.models import Message
from store_backend.services import campaign_pricing
from store_backend.services import stock
from store_backend.services import stock_alerts
from store_backend.shop.models import StockAlert
from store_backend.shop.tests.test_orders import place

pytestmark = pytest.mark.django_db

EMAIL = "ayse@example.com"


@pytest.fixture
def sold_out(shop):
    return shop[1]["L"]


def waiting(variant, email=EMAIL, *, confirmed=True, expired=False):
    now = timezone.now()
    delta = timedelta(days=-1) if expired else timedelta(days=30)
    return StockAlert.objects.create(
        email=email,
        variant=variant,
        notice_version="1",
        confirm_token=f"t-{email}-{variant.pk}",
        confirmed_at=now if confirmed else None,
        expires_at=now + delta,
    )


def emails():
    return list(
        Message.objects.filter(template="stock_alert_back")
        .order_by("pk")
        .values_list("recipient", flat=True),
    )


def test_adjust_from_zero_emails_everyone_waiting_then_deletes_the_rows(
    sold_out,
    owner,
):
    waiting(sold_out, "a@example.com")
    waiting(sold_out, "b@example.com")

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert emails() == ["a@example.com", "b@example.com"]
    assert not StockAlert.objects.exists()


def test_adjust_many_fires(shop, sold_out, owner):
    waiting(sold_out)

    stock.adjust_many(owner, shop[0], changes={sold_out.pk: 2}, reason="sayım")

    assert emails() == [EMAIL]


def test_one_to_two_does_not_fire(shop, owner):
    variant = shop[1]["M"]  # 5 in stock
    waiting(variant)

    stock.adjust(owner, variant, delta=1, reason="sayım")

    assert emails() == []
    assert StockAlert.objects.count() == 1


def test_reducing_stock_does_not_fire(shop, owner):
    waiting(shop[1]["L"])

    with pytest.raises(Exception):  # noqa: B017, PT011
        stock.adjust(owner, shop[1]["L"], delta=-1, reason="sayım")

    assert emails() == []


def test_a_cancelled_order_releasing_the_last_units_fires(shop):
    variant = shop[1]["M"]
    StockRecord.objects.filter(product=variant).update(num_in_stock=2)
    order = Order.objects.get(number=place(variant))  # reserves both
    assert stock.units(stock.record_for(variant)) == 0
    waiting(variant)

    stock.release(order)

    assert emails() == [EMAIL]


def test_a_restocked_return_fires(shop, owner):
    variant = shop[1]["M"]
    StockRecord.objects.filter(product=variant).update(num_in_stock=2)
    order = Order.objects.get(number=place(variant))
    stock.consume(order)  # shipped: shelf is empty
    assert stock.units(stock.record_for(variant)) == 0
    waiting(variant)

    stock.restock(owner, [(order.lines.get(), 1)], reason="iade")

    assert emails() == [EMAIL]


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.ARCHIVED])
def test_unpublished_products_do_not_alert(shop, sold_out, owner, status):
    waiting(sold_out)
    ProductProfile.objects.filter(product=shop[0]).update(status=status)

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert emails() == []


def test_unconfirmed_and_expired_rows_are_skipped(sold_out, owner):
    waiting(sold_out, "pending@example.com", confirmed=False)
    waiting(sold_out, "old@example.com", expired=True)
    waiting(sold_out, "ok@example.com")

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert emails() == ["ok@example.com"]


def test_other_variants_waiters_are_left_alone(shop, sold_out, owner):
    waiting(shop[1]["M"], "m@example.com")
    waiting(sold_out, "l@example.com")

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert emails() == ["l@example.com"]
    assert StockAlert.objects.get().email == "m@example.com"


def test_the_email_has_what_the_spec_lists_and_nothing_else(sold_out, owner):
    waiting(sold_out)

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    message = Message.objects.get(template="stock_alert_back")
    text = message.body_text
    assert "Elbise" in text
    assert "Siyah" in text
    assert "L" in text
    assert "100,00" in text
    assert "/urun/" in text
    assert "Sınırlı stok" in text
    assert stock_alerts.reject_token(EMAIL, sold_out.pk) in text
    assert "/stok-bildirimi/vazgec?token=" in text


def test_the_email_shows_the_campaign_price(shop, sold_out, owner, monkeypatch):
    from decimal import Decimal  # noqa: PLC0415

    monkeypatch.setattr(
        campaign_pricing,
        "unit_price",
        lambda variant, **_: Decimal("80.00"),
    )
    waiting(sold_out)

    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert "80,00" in Message.objects.get(template="stock_alert_back").body_text


def test_no_waiters_means_no_work(sold_out, owner):
    stock.adjust(owner, sold_out, delta=3, reason="sayım")

    assert emails() == []
