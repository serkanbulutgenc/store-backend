from __future__ import annotations

from decimal import Decimal

import pytest
from oscar.apps.offer.models import Benefit

from store_backend.services import pricing

D = Decimal

pytestmark = pytest.mark.django_db


def _line(price: str, rate: int, quantity: int = 1) -> pricing.LineSnapshot:
    return pricing.line_snapshot(
        unit_price=D(price),
        quantity=quantity,
        kdv_rate=D(rate),
    )


def test_discount_is_spread_over_lines_in_proportion_to_their_value():
    lines = [_line("100.00", 20), _line("50.00", 10), _line("25.00", 10)]

    discounted = pricing.apply_discount(lines, D("17.50"))

    assert [line.total_incl for line in discounted] == [
        D("90.00"),
        D("45.00"),
        D("22.50"),
    ]
    # Worked by hand: 90.00 / 1.20 = 75.00; 45.00 / 1.10 = 40.909 -> 40.91;
    # 22.50 / 1.10 = 20.4545 -> 20.45. KDV is the remainder of each line.
    assert [(line.total_excl, line.total_kdv) for line in discounted] == [
        (D("75.00"), D("15.00")),
        (D("40.91"), D("4.09")),
        (D("20.45"), D("2.05")),
    ]


def test_leftover_kurus_goes_to_the_largest_line_so_the_discount_is_exact():
    lines = [_line("30.00", 20), _line("20.00", 20), _line("20.00", 20)]

    discounted = pricing.apply_discount(lines, D("20.00"))

    # Pro-rata shares round to 8.57 + 5.71 + 5.71 = 19.99: one kurus is missing, and
    # it is taken off the largest line (8.58), not spread or dropped.
    assert [line.total_incl for line in discounted] == [
        D("21.42"),
        D("14.29"),
        D("14.29"),
    ]
    assert sum(line.total_incl for line in discounted) == D("50.00")


def test_discount_larger_than_the_items_never_takes_a_line_below_zero():
    lines = [_line("20.00", 20), _line("10.00", 10)]

    discounted = pricing.apply_discount(lines, D("50.00"))

    assert [line.total_incl for line in discounted] == [D("0.00"), D("0.00")]
    assert [(line.total_excl, line.total_kdv) for line in discounted] == [
        (D("0.00"), D("0.00")),
        (D("0.00"), D("0.00")),
    ]


def test_percentage_voucher_is_a_share_of_the_items_rounded_half_up_to_kurus():
    assert pricing.voucher_discount(D("175.00"), Benefit.PERCENTAGE, D(10)) == D(
        "17.50",
    )
    # 15% of 10.30 = 1.545, which rounds up to 1.55 (not to the even 1.54).
    assert pricing.voucher_discount(D("10.30"), Benefit.PERCENTAGE, D(15)) == D(
        "1.55",
    )


def test_fixed_voucher_is_its_amount_whatever_the_items_cost():
    assert pricing.voucher_discount(D("200.00"), Benefit.FIXED, D("50.00")) == D(
        "50.00",
    )


def test_discounting_nothing_leaves_nothing():
    assert pricing.apply_discount([], D("10.00")) == []
    free = [_line("0.00", 20)]
    assert pricing.apply_discount(free, D("10.00")) == free
