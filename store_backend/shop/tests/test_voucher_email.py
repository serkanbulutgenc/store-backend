"""The order confirmation email, for an Order that used a Voucher."""

from __future__ import annotations

import pytest
from oscar.apps.offer.models import Benefit

from store_backend.messaging.models import Message
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import guest  # noqa: F401
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_checkout import set_fees

pytestmark = pytest.mark.django_db

VOUCHER = "/api/store/basket/voucher"


def test_the_email_adds_up_with_the_voucher_between_the_lines_and_the_shipping(
    guest,  # noqa: F811
    owner,
):
    set_fees(owner, shipping_fee="30.00", free_shipping_threshold=None)
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    guest.post(VOUCHER, {"code": "YAZ10"})  # 2 x 100.00

    assert guest.post(CHECKOUT, payload()).status_code == 201  # noqa: PLR2004
    mail = Message.objects.get(template="order_placed")

    text = mail.body_text
    assert "x 2: 200,00 TL" in text  # the line at its price before the voucher
    assert "Kupon (YAZ10): -20,00 TL" in text
    assert "Toplam: 210,00 TL" in text
    assert text.index("x 2:") < text.index("Kupon (YAZ10)") < text.index("Kargo:")
    assert "Kupon (YAZ10)" in mail.body_html
    assert "-20,00 TL" in mail.body_html


def test_an_order_without_a_voucher_gets_the_email_it_always_did(guest, owner):  # noqa: F811
    set_fees(owner, shipping_fee="30.00", free_shipping_threshold=None)

    assert guest.post(CHECKOUT, payload()).status_code == 201  # noqa: PLR2004
    mail = Message.objects.get(template="order_placed")

    assert "x 2: 200,00 TL" in mail.body_text
    assert "Toplam: 230,00 TL" in mail.body_text
    assert "Kupon" not in mail.body_text
    assert "Kupon" not in mail.body_html
