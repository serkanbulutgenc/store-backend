"""Owner-only Reports: Returns, and Campaigns and Vouchers (store-infra#79)."""

# ruff: noqa: F401, F811, PLR2004
import csv
import io
from http import HTTPStatus

import pytest
from oscar.apps.offer.models import Benefit
from oscar.apps.partner.models import StockRecord

from store_backend.services import audit
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_campaign_basket import campaign
from store_backend.shop.tests.test_campaign_order import place as place_campaign
from store_backend.shop.tests.test_orders import act
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import staff
from store_backend.shop.tests.test_reports import receive
from store_backend.shop.tests.test_reports import two_products
from store_backend.shop.tests.test_returns import place as place_units
from store_backend.shop.tests.test_returns import request_return
from store_backend.shop.tests.test_voucher_returns import deliver
from store_backend.shop.tests.test_voucher_returns import place_with_voucher
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

RETURNS = "/api/admin/reports/returns"
CAMPAIGNS = "/api/admin/reports/campaigns-vouchers"
URLS = [RETURNS, f"{RETURNS}.csv", CAMPAIGNS, f"{CAMPAIGNS}.csv"]


@pytest.mark.parametrize("url", URLS)
def test_staff_get_403(url, staff):
    assert staff.get(url).status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("url", URLS)
def test_anonymous_gets_401(url, client):
    assert client.get(url).status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize("url", URLS)
def test_the_owner_gets_a_report(url, boss):
    assert boss.get(url).status_code == HTTPStatus.OK


# --- return rate by Product -----------------------------------


def shipped_order(shop, staff, quantity, size="M"):
    """An Order of `quantity` units of Elbise, paid, shipped and delivered."""
    variant = shop[1][size]
    StockRecord.objects.filter(product=variant).update(num_in_stock=100)
    placed = place_units(variant, quantity=quantity)
    deliver(staff, placed)
    return placed


def by_product(response):
    return [
        (r["title"], r["shipped"], r["returned"], r["rate"])
        for r in response.json()["by_product"]
    ]


def test_the_rate_is_returned_units_over_units_shipped(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    receive(staff, placed, quantity=1)

    assert by_product(boss.get(RETURNS)) == [("Elbise", 5, 1, 20.0)]


def test_a_row_under_5_shipped_units_carries_counts_only(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=4)
    receive(staff, placed, quantity=1)

    assert by_product(boss.get(RETURNS)) == [("Elbise", 4, 1, None)]


def test_a_return_still_waiting_is_not_counted(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    request_return(placed, quantity=1)

    assert by_product(boss.get(RETURNS)) == [("Elbise", 5, 0, 0.0)]


def test_an_order_not_yet_shipped_is_not_shipped_units(shop, boss):
    place_units(shop[1]["M"], quantity=5)  # placed, never shipped

    assert by_product(boss.get(RETURNS)) == []


# --- return rate by Size ----------------------------------


def by_size(response):
    return [
        (r["size"], r["shipped"], r["returned"], r["rate"])
        for r in response.json()["by_size"]
    ]


def test_the_rate_is_also_given_by_size(shop, staff, boss):
    in_m = shipped_order(shop, staff, quantity=5, size="M")
    in_l = shipped_order(shop, staff, quantity=5, size="L")
    receive(staff, in_m, quantity=1)
    receive(staff, in_l, quantity=2)

    # The Size with the most returns comes first.
    assert by_size(boss.get(RETURNS)) == [("L", 5, 2, 40.0), ("M", 5, 1, 20.0)]


def test_one_size_is_one_row_across_products(two_products, staff, boss):
    _, variants, _, trousers = two_products
    StockRecord.objects.filter(product=trousers).update(num_in_stock=100)
    dress = place_units(variants["M"], quantity=3)
    trouser = place_units(trousers, quantity=2)
    for placed in (dress, trouser):
        deliver(staff, placed)
    receive(staff, trouser, quantity=1)

    # Elbise M (3 shipped) and Pantolon M (2 shipped) are both Size M.
    assert by_size(boss.get(RETURNS)) == [("M", 5, 1, 20.0)]
    assert len(boss.get(RETURNS).json()["by_product"]) == 2


def test_a_size_under_5_units_carries_counts_only(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=4, size="M")
    receive(staff, placed, quantity=1)

    assert by_size(boss.get(RETURNS)) == [("M", 4, 1, None)]


# --- reason mix -----------------------------------------

NO_REASONS = {
    "wrong_size": 0,
    "changed_mind": 0,
    "defective": 0,
    "wrong_item": 0,
    "other": 0,
    "unspecified": 0,
}


def test_each_row_shows_why_units_came_back(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    receive(staff, placed, quantity=1, reason="wrong_size")
    receive(staff, placed, quantity=2, reason="defective")

    body = boss.get(RETURNS).json()

    expected = {**NO_REASONS, "wrong_size": 1, "defective": 2}
    assert body["by_product"][0]["reasons"] == expected
    assert body["by_size"][0]["reasons"] == expected


def test_a_row_with_no_returns_has_every_reason_at_zero(shop, staff, boss):
    shipped_order(shop, staff, quantity=5)

    assert boss.get(RETURNS).json()["by_product"][0]["reasons"] == NO_REASONS


def test_the_reasons_add_up_to_the_returned_units(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    receive(staff, placed, quantity=1, reason="changed_mind")
    receive(staff, placed, quantity=1, reason="wrong_item")

    row = boss.get(RETURNS).json()["by_product"][0]

    assert sum(row["reasons"].values()) == row["returned"] == 2


# --- whole-order returns ------------------------------------


def return_whole_order(staff, placed):
    number = placed["number"]
    assert act(staff, number, "request-return").status_code == HTTPStatus.OK
    assert act(staff, number, "mark-returned").status_code == HTTPStatus.OK


def test_a_whole_order_return_counts_every_unit_with_no_stated_reason(
    shop,
    staff,
    boss,
):
    placed = shipped_order(shop, staff, quantity=5)
    return_whole_order(staff, placed)

    body = boss.get(RETURNS).json()

    expected = {**NO_REASONS, "unspecified": 5}
    for view in ("by_product", "by_size"):
        row = body[view][0]
        assert (row["shipped"], row["returned"], row["rate"]) == (5, 5, 100.0)
        assert row["reasons"] == expected


def test_units_already_returned_by_request_are_not_counted_twice(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    receive(staff, placed, quantity=1, reason="wrong_size")
    return_whole_order(staff, placed)

    row = boss.get(RETURNS).json()["by_product"][0]

    # One unit came back first, with its reason; the other four are the rest.
    assert (row["shipped"], row["returned"]) == (5, 5)
    assert row["reasons"] == {**NO_REASONS, "wrong_size": 1, "unspecified": 4}


def test_a_whole_order_return_still_waiting_is_not_counted(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    assert act(staff, placed["number"], "request-return").status_code == HTTPStatus.OK

    assert by_product(boss.get(RETURNS)) == [("Elbise", 5, 0, 0.0)]


# --- Campaigns -------------------------------------------


def paid(staff, placed):
    act(staff, placed["number"], "pay", {"amount": placed["total"]})
    return placed


def campaign_rows(response):
    return [
        (r["name"], r["orders"], r["discount"], r["revenue"])
        for r in response.json()["campaigns"]
    ]


def test_a_campaign_shows_orders_discount_given_and_revenue(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    paid(staff, place_campaign(variants["M"]))  # 2 x 100.00 at 20% off

    # Discount: (100.00 - 80.00) x 2 = 40.00. Revenue is what was paid: 160.00.
    assert campaign_rows(boss.get(CAMPAIGNS)) == [("Kara Cuma", 1, "40.00", "160.00")]


def test_an_order_without_a_campaign_is_not_listed(shop, staff, boss):
    paid(staff, place_campaign(shop[1]["M"]))

    assert campaign_rows(boss.get(CAMPAIGNS)) == []


def test_an_unpaid_transfer_is_not_counted(shop, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    place_campaign(variants["M"])  # placed, never paid

    assert campaign_rows(boss.get(CAMPAIGNS)) == []


# --- Vouchers --------------------------------------------


def voucher_rows(response):
    return [
        (r["code"], r["orders"], r["discount"], r["revenue"])
        for r in response.json()["vouchers"]
    ]


def test_a_voucher_shows_orders_discount_given_and_revenue(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    paid(staff, place_with_voucher(shop[1]["M"]))  # 2 x 100.00, 20.00 off

    assert voucher_rows(boss.get(CAMPAIGNS)) == [("YAZ10", 1, "20.00", "180.00")]


def test_a_voucher_adds_up_its_orders(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    paid(staff, place_with_voucher(shop[1]["M"]))
    paid(staff, place_with_voucher(shop[1]["M"]))

    assert voucher_rows(boss.get(CAMPAIGNS)) == [("YAZ10", 2, "40.00", "360.00")]


def test_an_order_without_a_voucher_is_not_listed(shop, staff, boss):
    paid(staff, place_campaign(shop[1]["M"]))

    assert voucher_rows(boss.get(CAMPAIGNS)) == []


def test_an_unpaid_voucher_order_is_not_counted(shop, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    place_with_voucher(shop[1]["M"])  # placed, never paid

    assert voucher_rows(boss.get(CAMPAIGNS)) == []


# --- returns come off campaign and voucher revenue ------------------------


def test_a_returned_unit_comes_off_a_vouchers_revenue_at_what_was_paid(
    shop,
    staff,
    boss,
):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])  # 2 x 100.00, 20.00 off: 180.00
    deliver(staff, placed)
    receive(staff, placed, quantity=1)

    # The unit was paid for at 90.00. The discount given stays as it was.
    assert voucher_rows(boss.get(CAMPAIGNS)) == [("YAZ10", 1, "20.00", "90.00")]


def test_a_returned_unit_comes_off_a_campaigns_revenue(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    placed = place_campaign(variants["M"])  # 2 x 80.00
    deliver(staff, placed)
    receive(staff, placed, quantity=1)

    assert campaign_rows(boss.get(CAMPAIGNS)) == [("Kara Cuma", 1, "40.00", "80.00")]


def test_a_whole_order_return_removes_the_order_from_both_lists(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_campaign(variants["M"], voucher="YAZ10")
    deliver(staff, placed)
    return_whole_order(staff, placed)

    body = boss.get(CAMPAIGNS).json()

    assert (body["campaigns"], body["vouchers"]) == ([], [])


# --- a campaign and a voucher on one order -------------------


def test_each_discount_is_credited_to_its_own_source(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    # 2 x 100.00 -> 160.00 with the Campaign -> 144.00 with the Voucher (16.00 off).
    paid(staff, place_campaign(variants["M"], voucher="YAZ10"))

    body = boss.get(CAMPAIGNS)

    assert campaign_rows(body) == [("Kara Cuma", 1, "40.00", "144.00")]
    assert voucher_rows(body) == [("YAZ10", 1, "16.00", "144.00")]


# --- KDV choice -----------------------------------------------


def test_the_campaign_rows_without_kdv(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    paid(staff, place_campaign(variants["M"]))  # 160.00 incl, 40.00 off; KDV 10%

    body = boss.get(f"{CAMPAIGNS}?kdv=excl")

    # 160.00 / 1.10 = 145.45 and 40.00 / 1.10 = 36.36
    assert campaign_rows(body) == [("Kara Cuma", 1, "36.36", "145.45")]
    assert body.json()["kdv"] == "excl"


def test_the_voucher_rows_without_kdv(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    paid(staff, place_with_voucher(shop[1]["M"]))  # 180.00 incl, 20.00 off; KDV 10%

    # 180.00 / 1.10 = 163.64; the Voucher took 181.82 - 163.64 = 18.18 off.
    assert voucher_rows(boss.get(f"{CAMPAIGNS}?kdv=excl")) == [
        ("YAZ10", 1, "18.18", "163.64"),
    ]


def test_kdv_inclusive_is_the_default_here_too(shop, staff, boss):
    paid(staff, place_campaign(shop[1]["M"]))

    assert boss.get(CAMPAIGNS).json()["kdv"] == "incl"


def test_a_returned_unit_nets_out_without_kdv_too(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])
    deliver(staff, placed)
    receive(staff, placed, quantity=1)

    # 163.64 less half of it (81.82) is 81.82.
    assert voucher_rows(boss.get(f"{CAMPAIGNS}?kdv=excl"))[0][3] == "81.82"


# --- CSV -------------------------------------------------


def csv_rows(response):
    assert response.status_code == HTTPStatus.OK, response.content
    return list(csv.DictReader(io.StringIO(response.content.decode())))


def test_the_returns_csv_has_both_views_and_the_reason_columns(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    receive(staff, placed, quantity=1, reason="wrong_size")

    response = boss.get(f"{RETURNS}.csv")

    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    expected = {
        "shipped": "5",
        "returned": "1",
        "rate": "20.0",
        "wrong_size": "1",
        "changed_mind": "0",
        "defective": "0",
        "wrong_item": "0",
        "other": "0",
        "unspecified": "0",
    }
    assert csv_rows(response) == [
        {"view": "product", "name": "Elbise", **expected},
        {"view": "size", "name": "M", **expected},
    ]


def test_a_small_row_has_a_blank_rate_in_the_csv(shop, staff, boss):
    receive(staff, shipped_order(shop, staff, quantity=4), quantity=1)

    assert {r["rate"] for r in csv_rows(boss.get(f"{RETURNS}.csv"))} == {""}


def test_the_campaigns_and_vouchers_csv_lists_both_kinds(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    paid(staff, place_campaign(variants["M"], voucher="YAZ10"))

    response = boss.get(f"{CAMPAIGNS}.csv")

    assert csv_rows(response) == [
        {
            "kind": "campaign",
            "name": "Kara Cuma",
            "orders": "1",
            "discount": "40.00",
            "revenue": "144.00",
        },
        {
            "kind": "voucher",
            "name": "YAZ10",
            "orders": "1",
            "discount": "16.00",
            "revenue": "144.00",
        },
    ]


def test_a_campaign_name_that_looks_like_a_formula_is_defanged(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="=HYPERLINK(1)")
    paid(staff, place_campaign(variants["M"]))

    assert csv_rows(boss.get(f"{CAMPAIGNS}.csv"))[0]["name"].startswith("'=")


def test_the_csvs_are_audited_with_range_and_user(shop, boss, owner):
    boss.get(f"{RETURNS}.csv?date_from=2026-11-01&date_to=2026-11-30")
    boss.get(f"{CAMPAIGNS}.csv?preset=7d&kdv=excl")

    entries = AuditEntry.objects.filter(action=audit.Action.EXPORT_RUN)
    assert sorted(e.target_type for e in entries) == [
        "reports_campaigns_vouchers_csv",
        "reports_returns_csv",
    ]
    assert {e.actor_email for e in entries} == {owner.email}
    returns = entries.get(target_type="reports_returns_csv")
    assert (returns.after["from"], returns.after["to"]) == ("2026-11-01", "2026-11-30")
    assert (
        entries.get(target_type="reports_campaigns_vouchers_csv").after["kdv"] == "excl"
    )


def test_the_json_reports_are_not_audited(boss):
    boss.get(RETURNS)
    boss.get(CAMPAIGNS)

    assert not AuditEntry.objects.filter(action=audit.Action.EXPORT_RUN).exists()


# --- short cache ---------------------------------------------


def test_the_returns_report_asked_again_within_the_minute_is_cached(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    assert by_product(boss.get(RETURNS)) == [("Elbise", 5, 0, 0.0)]

    receive(staff, placed, quantity=1)

    assert by_product(boss.get(RETURNS)) == [("Elbise", 5, 0, 0.0)]  # still cached


def test_the_campaigns_report_asked_again_within_the_minute_is_cached(
    shop,
    staff,
    boss,
):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    paid(staff, place_campaign(variants["M"]))
    assert len(boss.get(CAMPAIGNS).json()["campaigns"]) == 1

    StockRecord.objects.filter(product=variants["M"]).update(num_in_stock=50)
    paid(staff, place_campaign(variants["M"]))

    assert boss.get(CAMPAIGNS).json()["campaigns"][0]["orders"] == 1  # still cached


def test_the_cache_is_per_query(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    paid(staff, place_campaign(variants["M"]))
    boss.get(CAMPAIGNS)

    excl = boss.get(f"{CAMPAIGNS}?kdv=excl").json()

    assert excl["campaigns"][0]["revenue"] == "145.45"


def test_the_csvs_are_never_cached(shop, staff, boss):
    placed = shipped_order(shop, staff, quantity=5)
    boss.get(RETURNS)  # fills the cache
    receive(staff, placed, quantity=1)

    assert csv_rows(boss.get(f"{RETURNS}.csv"))[0]["returned"] == "1"
