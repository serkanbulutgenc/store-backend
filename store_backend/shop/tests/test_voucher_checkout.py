"""Placing an Order with a Voucher code: what is charged, recorded and frozen."""

from __future__ import annotations

from decimal import Decimal

import pytest
from oscar.apps.basket.models import Basket
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import VoucherApplication
from oscar.core.loading import get_class

from store_backend.api.errors import ConflictError
from store_backend.services import checkout
from store_backend.services import pre_information
from store_backend.services import shop_settings
from store_backend.services.contact import Address
from store_backend.shop.models import PaymentMethod
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import fill

pytestmark = pytest.mark.django_db

Selector = get_class("partner.strategy", "Selector")

D = Decimal
GUEST = checkout.Caller(user=None, ip_address="203.0.113.7", user_agent="TestBrowser")


def open_basket(variant) -> Basket:
    """A new basket of 2 x 100.00, priced the way a request would price it."""
    fill(Guest(), variant, quantity=2)
    basket = Basket.objects.get(status=Basket.OPEN)
    basket.strategy = Selector().strategy()  # the request middleware does this
    return basket


@pytest.fixture
def basket(shop):
    return open_basket(shop[1]["M"])


def place(basket: Basket, voucher_code: str | None = None, *, email="ayse@example.com"):
    form = checkout.CheckoutForm(
        full_name="Ayşe Yılmaz",
        email=email,
        phone="0532 123 45 67",
        shipping_address=Address(
            il="Mersin",
            ilce="Yenişehir",
            mahalle="Çiftlikköy Mah.",
            street="Gül Sok. No:5 D:3",
            postal_code="33110",
        ),
        billing_address=None,
        payment_method=PaymentMethod.BANK_TRANSFER,
        contract_acknowledged=True,
        voucher_code=voucher_code,
    )
    return checkout.place_order(basket, form, GUEST)


def test_a_voucher_lowers_the_items_the_order_charges(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    shipping = shop_settings.get().shipping_fee

    order = place(basket, "yaz10")

    assert order.lines.get().line_price_incl_tax == D("180.00")
    assert order.total_incl_tax == D("180.00") + shipping
    assert Order.objects.count() == 1


def test_placing_the_order_uses_up_a_redemption(shop, basket):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="ONE10")
    voucher.offers.update(max_global_applications=1)

    order = place(basket, "ONE10")

    assert VoucherApplication.objects.get(voucher=voucher).order == order
    voucher.refresh_from_db()
    assert voucher.num_orders == 1
    with pytest.raises(ConflictError) as error:
        place(open_basket(shop[1]["M"]), "ONE10", email="deniz@example.com")
    assert error.value.fields == {"voucher_code": ["exhausted"]}


def test_a_code_that_stopped_working_refuses_the_order_and_leaves_the_basket(basket):
    make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="OLD10",
        starts_in_days=-10,
        ends_in_days=-1,
    )

    with pytest.raises(ConflictError) as error:
        place(basket, "OLD10")

    assert error.value.code == "voucher_invalid"
    assert error.value.fields == {"voucher_code": ["expired"]}
    assert Order.objects.count() == 0
    basket.refresh_from_db()
    assert basket.status == Basket.OPEN
    # The Shopper can carry on without the code.
    assert (
        place(basket).total_incl_tax == D("200.00") + shop_settings.get().shipping_fee
    )


def test_a_basket_below_the_minimum_refuses_the_order_and_uses_no_redemption(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="BIG10", minimum="250.00")

    with pytest.raises(ConflictError) as error:
        place(basket, "BIG10")  # the items cost 200.00

    assert error.value.code == "voucher_invalid"
    assert error.value.fields == {"voucher_code": ["minimum"]}
    assert Order.objects.count() == 0
    assert VoucherApplication.objects.count() == 0


def test_the_line_remembers_what_it_cost_before_the_voucher(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    line = place(basket, "YAZ10").lines.get()

    assert line.line_price_incl_tax == D("180.00")
    assert line.line_price_before_discounts_incl_tax == D("200.00")
    # Giyim is at 10% KDV: 200.00 / 1.10 = 181.82 before, 180.00 / 1.10 = 163.64 after.
    assert line.line_price_before_discounts_excl_tax == D("181.82")
    assert line.line_price_excl_tax == D("163.64")


def test_the_order_records_which_voucher_gave_how_much_off(basket):
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    discount = place(basket, "yaz10").discounts.get()

    assert discount.voucher_id == voucher.pk
    assert discount.voucher_code == "YAZ10"
    assert discount.amount == D("20.00")
    assert discount.offer_name == "offer-YAZ10"


def test_the_pre_information_snapshot_shows_the_voucher_and_what_it_took_off(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    shipping = shop_settings.get().shipping_fee

    text = place(basket, "YAZ10").pre_information.text

    assert "Ürünler toplamı: 200,00 TL" in text
    assert "Kupon (YAZ10): -20,00 TL" in text
    assert (
        f"Toplam (KDV dahil): {pre_information.format_try(D(180) + shipping)}" in text
    )
    # It sits between the items total and the shipping fee.
    assert text.index("Ürünler toplamı") < text.index("Kupon (YAZ10)")
    assert text.index("Kupon (YAZ10)") < text.index("Kargo ücreti")


def test_the_snapshot_lists_lines_at_their_price_before_the_voucher(basket):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    text = place(basket, "YAZ10").pre_information.text

    # The lines add up to "Ürünler toplamı"; the Kupon line then takes 20,00 off.
    assert "x 2: birim 100,00 TL, toplam 200,00 TL (KDV %10.00 dahil)" in text
    assert "toplam 180,00 TL" not in text
