"""Checking a Voucher code a Shopper typed: who may use it, and why not."""

from __future__ import annotations

import pytest
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.voucher.models import Voucher

from store_backend.services import vouchers
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.conftest import redeem
from store_backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_a_valid_code_is_found_whatever_its_case_and_spacing():
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    found = vouchers.validate("  yaz10 ", user=None, email="ayse@example.com")

    assert found == voucher


def test_an_unknown_code_is_invalid():
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")

    with pytest.raises(vouchers.VoucherError) as error:
        vouchers.validate("KIS20", user=None, email="ayse@example.com")

    assert error.value.reason == vouchers.Reason.INVALID


def test_a_code_outside_its_dates_is_expired():
    make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="OLD10",
        starts_in_days=-10,
        ends_in_days=-1,
    )
    make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="SOON10",
        starts_in_days=2,
        ends_in_days=9,
    )

    for code in ("OLD10", "SOON10"):
        with pytest.raises(vouchers.VoucherError) as error:
            vouchers.validate(code, user=None, email="ayse@example.com")
        assert error.value.reason == vouchers.Reason.EXPIRED


def test_a_deactivated_code_is_inactive_not_expired():
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="PAUSED10")
    voucher.offers.update(status=ConditionalOffer.SUSPENDED)

    with pytest.raises(vouchers.VoucherError) as error:
        vouchers.validate("PAUSED10", user=None, email="ayse@example.com")

    assert error.value.reason == vouchers.Reason.INACTIVE


def test_a_code_whose_redemption_cap_is_reached_is_exhausted():
    voucher = make_voucher(Benefit.PERCENTAGE, "10", code="TWO10")
    voucher.offers.update(max_global_applications=2)
    redeem(voucher)

    assert vouchers.validate("TWO10", user=None, email="b@example.com") == voucher

    redeem(voucher)

    with pytest.raises(vouchers.VoucherError) as error:
        vouchers.validate("TWO10", user=None, email="c@example.com")
    assert error.value.reason == vouchers.Reason.EXHAUSTED


def test_once_per_customer_refuses_a_customer_who_has_used_it(user):
    voucher = make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    other = UserFactory.create()
    redeem(voucher, user=user, email=user.email)

    with pytest.raises(vouchers.VoucherError) as error:
        vouchers.validate("ONCE10", user=user, email=user.email)
    assert error.value.reason == vouchers.Reason.ALREADY_USED

    assert vouchers.validate("ONCE10", user=other, email=other.email) == voucher


def test_once_per_customer_recognises_a_guest_by_the_email_on_the_order(user):
    voucher = make_voucher(
        Benefit.PERCENTAGE,
        "10",
        code="ONCE10",
        usage=Voucher.ONCE_PER_CUSTOMER,
    )
    redeem(voucher, email="Ayse@Example.com")  # a Guest's Order

    for email in ("ayse@example.com", "  AYSE@example.com "):
        with pytest.raises(vouchers.VoucherError) as error:
            vouchers.validate("ONCE10", user=None, email=email)
        assert error.value.reason == vouchers.Reason.ALREADY_USED

    assert vouchers.validate("ONCE10", user=None, email="deniz@example.com") == voucher

    # Logging in afterwards does not give a second use.
    with pytest.raises(vouchers.VoucherError) as error:
        vouchers.validate("ONCE10", user=user, email="ayse@example.com")
    assert error.value.reason == vouchers.Reason.ALREADY_USED
