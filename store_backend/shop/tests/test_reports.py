"""Owner-only Reports: Sales and Products (store-infra#78)."""

# ruff: noqa: F401, F811
import csv
import io
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from http import HTTPStatus
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.offer.models import Benefit
from oscar.apps.order.models import Order
from oscar.apps.partner.models import StockRecord

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import reports
from store_backend.shop.models import StoreSettings
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_campaign_basket import campaign
from store_backend.shop.tests.test_campaign_order import place as place_campaign
from store_backend.shop.tests.test_orders import act
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import cod
from store_backend.shop.tests.test_orders import place
from store_backend.shop.tests.test_orders import staff
from store_backend.shop.tests.test_orders import transfer
from store_backend.shop.tests.test_return_review import delivered
from store_backend.shop.tests.test_return_review import filed
from store_backend.shop.tests.test_return_review import review
from store_backend.shop.tests.test_returns import place as place_units
from store_backend.shop.tests.test_voucher_returns import (
    deliver as deliver_voucher_order,
)
from store_backend.shop.tests.test_voucher_returns import place_with_voucher
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

SALES = "/api/admin/reports/sales"
PRODUCTS = "/api/admin/reports/products"


@pytest.mark.parametrize("url", [SALES, PRODUCTS])
def test_staff_get_403(url, staff):
    assert staff.get(url).status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("url", [SALES, PRODUCTS])
def test_anonymous_gets_401(url, client):
    assert client.get(url).status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize("url", [SALES, PRODUCTS])
def test_the_owner_gets_a_report(url, boss):
    assert boss.get(url).status_code == HTTPStatus.OK


# --- ranges ----------------------------------------

SATURDAY = date(2026, 11, 21)


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(reports, "today", lambda: SATURDAY)


def periods(response):
    body = response.json()
    return (
        (body["range"]["start"], body["range"]["end"]),
        (body["previous"]["start"], body["previous"]["end"]),
    )


def test_the_default_is_the_last_30_days_against_the_30_before(boss, today):
    assert periods(boss.get(SALES)) == (
        ("2026-10-23", "2026-11-21"),
        ("2026-09-23", "2026-10-22"),
    )


@pytest.mark.parametrize(
    ("preset", "current", "previous"),
    [
        ("today", ("2026-11-21", "2026-11-21"), ("2026-11-20", "2026-11-20")),
        ("7d", ("2026-11-15", "2026-11-21"), ("2026-11-08", "2026-11-14")),
        ("30d", ("2026-10-23", "2026-11-21"), ("2026-09-23", "2026-10-22")),
        ("this_month", ("2026-11-01", "2026-11-21"), ("2026-10-11", "2026-10-31")),
        ("last_month", ("2026-10-01", "2026-10-31"), ("2026-08-31", "2026-09-30")),
    ],
)
def test_presets(preset, current, previous, boss, today):
    response = boss.get(f"{SALES}?preset={preset}")

    assert periods(response) == (current, previous)


def test_a_custom_range_is_compared_with_the_days_just_before_it(boss, today):
    response = boss.get(f"{SALES}?date_from=2026-11-10&date_to=2026-11-12")

    assert periods(response) == (
        ("2026-11-10", "2026-11-12"),
        ("2026-11-07", "2026-11-09"),
    )


@pytest.mark.parametrize(
    "query",
    [
        "date_from=2026-11-12&date_to=2026-11-10",  # backwards
        "date_from=2025-01-01&date_to=2026-11-10",  # over 366 days
        "date_from=2026-11-10",  # half a range
        "preset=yesterday",  # not a preset
    ],
)
def test_a_bad_range_is_refused(query, boss, today):
    assert boss.get(f"{SALES}?{query}").status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- what counts as a sale ----------------------------------------


def totals(response, key="totals"):
    return response.json()[key]


def test_a_cod_order_counts_at_placement(cod, boss):
    # Shelf price 100.00 x 2 units; the COD fee is reported apart, not in revenue.
    assert totals(boss.get(SALES)) == {
        "orders": 1,
        "items": 2,
        "revenue": "200.00",
        "average_order": "200.00",
    }


NOTHING = {"orders": 0, "items": 0, "revenue": "0.00", "average_order": "0.00"}


def test_a_cancelled_order_is_not_a_sale(cod, boss):
    act(boss, cod, "cancel", {"reason": "Müşteri vazgeçti"})

    assert totals(boss.get(SALES)) == NOTHING


def test_an_unpaid_transfer_is_pending_not_a_sale(transfer, boss):
    body = boss.get(SALES).json()

    assert body["totals"] == NOTHING
    assert body["pending"] == {"orders": 1, "revenue": "200.00"}


def test_a_paid_transfer_is_a_sale_and_no_longer_pending(transfer, boss):
    act(boss, transfer, "pay", {"amount": "200.00"})

    body = boss.get(SALES).json()

    assert body["totals"]["orders"] == 1
    assert body["totals"]["revenue"] == "200.00"
    assert body["pending"] == {"orders": 0, "revenue": "0.00"}


def test_a_transfer_past_its_48_hours_is_not_pending(transfer, boss):
    Order.objects.update(date_placed=timezone.now() - timedelta(hours=49))

    assert boss.get(SALES).json()["pending"]["orders"] == 0


# --- returns are netted out ----------------------------------------


def receive(staff, placed, quantity, reason="wrong_size"):
    """One unit-count return request, approved and received (not yet refunded)."""
    request = filed(placed, quantity=quantity, reason=reason)
    review(staff, placed, request.pk, "approve")
    line = request.lines.get().order_line_id
    done = review(
        staff,
        placed,
        request.pk,
        "receive",
        {"lines": [{"line_id": line, "restock": True}]},
    )
    assert done.status_code == HTTPStatus.OK, done.content


def test_a_received_return_comes_off_items_and_revenue(delivered, staff, boss):
    receive(staff, delivered, quantity=1)

    assert totals(boss.get(SALES)) == {
        "orders": 1,
        "items": 1,
        "revenue": "100.00",
        "average_order": "100.00",
    }


def test_a_return_still_waiting_is_not_netted(delivered, boss):
    filed(delivered, quantity=1)

    assert totals(boss.get(SALES))["revenue"] == "200.00"


def test_a_whole_order_return_removes_the_order(delivered, staff, boss):
    number = delivered["number"]
    act(staff, number, "request-return")
    assert act(staff, number, "mark-returned").status_code == HTTPStatus.OK

    assert totals(boss.get(SALES)) == NOTHING


# --- Voucher and KDV ----------------------------------------


def test_revenue_is_what_the_shopper_paid_after_the_voucher(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])  # 2 x 100.00 less 20.00
    act(staff, placed["number"], "pay", {"amount": placed["total"]})

    assert totals(boss.get(SALES))["revenue"] == "180.00"


def test_a_returned_unit_nets_what_was_paid_for_it(shop, staff, boss):
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_with_voucher(shop[1]["M"])
    deliver_voucher_order(staff, placed)
    receive(staff, placed, quantity=1)

    body = totals(boss.get(SALES))

    assert (body["items"], body["revenue"]) == (1, "90.00")  # not 100.00 off


def test_revenue_without_kdv_is_the_kdv_exclusive_amount(cod, boss):
    body = boss.get(f"{SALES}?kdv=excl").json()

    assert body["kdv"] == "excl"
    # 200.00 at 10% KDV: 200 / 1.10 = 181.82
    assert body["totals"]["revenue"] == "181.82"


def test_kdv_inclusive_is_the_default(cod, boss):
    assert boss.get(SALES).json()["kdv"] == "incl"


# --- fees ----------------------------------------


@pytest.fixture
def with_fees(shop):
    StoreSettings.objects.update_or_create(
        pk=1,
        defaults={"shipping_fee": "30.00", "cod_fee": "15.00"},
    )
    return place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)


def test_shipping_and_cod_fees_are_reported_apart_from_revenue(with_fees, boss):
    body = boss.get(SALES).json()

    assert body["totals"]["revenue"] == "200.00"
    assert body["fees"] == {"shipping": "30.00", "cod": "15.00"}


def test_fees_follow_the_kdv_choice(with_fees, boss):
    # Fee KDV is 20%: 30.00 -> 25.00 and 15.00 -> 12.50.
    body = boss.get(f"{SALES}?kdv=excl").json()

    assert body["fees"] == {"shipping": "25.00", "cod": "12.50"}


def test_a_cancelled_order_brings_no_fees(with_fees, boss):
    number = Order.objects.get().number
    act(boss, number, "cancel", {"reason": "Müşteri vazgeçti"})

    assert boss.get(SALES).json()["fees"] == {"shipping": "0.00", "cod": "0.00"}


# --- days are Istanbul days ----------------------------------------


def test_the_day_turns_over_at_midnight_in_istanbul(shop, boss, today):
    late = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    early = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    # 23:30 on the 20th and 00:30 on the 21st, Istanbul time (UTC+3): both are the
    # 20th in UTC, so a UTC day would put them on the same side.
    for number, stamp in (
        (late, datetime(2026, 11, 20, 20, 30, tzinfo=UTC)),
        (early, datetime(2026, 11, 20, 21, 30, tzinfo=UTC)),
    ):
        Order.objects.filter(number=number).update(date_placed=stamp)

    on_the_21st = boss.get(f"{SALES}?preset=today").json()
    on_the_20th = boss.get(f"{SALES}?date_from=2026-11-20&date_to=2026-11-20").json()

    assert on_the_21st["totals"]["orders"] == 1
    assert on_the_20th["totals"]["orders"] == 1
    assert on_the_21st["previous_totals"]["orders"] == 1  # the 20th, as "before"


# --- Campaign ----------------------------------------


def test_revenue_is_the_campaign_price_not_the_shelf_price(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    placed = place_campaign(variants["M"])  # 2 x 80.00
    act(staff, placed["number"], "pay", {"amount": placed["total"]})

    assert totals(boss.get(SALES))["revenue"] == "160.00"


def test_a_campaign_and_a_voucher_stack_in_revenue(shop, staff, boss):
    product, variants = shop
    campaign(20, product, name="Kara Cuma")
    make_voucher(Benefit.PERCENTAGE, "10", code="YAZ10")
    placed = place_campaign(variants["M"], voucher="YAZ10")  # 160.00 less 10%
    act(staff, placed["number"], "pay", {"amount": placed["total"]})

    assert totals(boss.get(SALES))["revenue"] == "144.00"


# --- rows by day, week and month ----------------------------


def placed_on(shop, *stamps):
    """One COD order (2 x 100.00) per (y, m, d, h), placed at that Istanbul time."""
    zone = ZoneInfo("Europe/Istanbul")
    StockRecord.objects.filter(product=shop[1]["M"]).update(num_in_stock=50)
    numbers = []
    for stamp in stamps:
        number = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
        Order.objects.filter(number=number).update(
            date_placed=datetime(*stamp, tzinfo=zone),
        )
        numbers.append(number)
    return numbers


def rows(response):
    return [
        (r["period"], r["orders"], r["items"], r["revenue"])
        for r in response.json()["rows"]
    ]


def test_rows_by_day_fill_the_days_with_no_sales(shop, boss):
    placed_on(
        shop,
        (2026, 11, 15, 12),
        (2026, 11, 16, 9),
        (2026, 11, 16, 18),
    )

    response = boss.get(f"{SALES}?date_from=2026-11-15&date_to=2026-11-17")

    assert rows(response) == [
        ("2026-11-15", 1, 2, "200.00"),
        ("2026-11-16", 2, 4, "400.00"),
        ("2026-11-17", 0, 0, "0.00"),
    ]


def test_weeks_start_on_monday(shop, boss):
    placed_on(
        shop,
        (2026, 11, 15, 12),  # a Sunday
        (2026, 11, 16, 9),  # the Monday after
    )

    response = boss.get(
        f"{SALES}?date_from=2026-11-15&date_to=2026-11-17&group=week",
    )

    assert rows(response) == [
        ("2026-11-09", 1, 2, "200.00"),
        ("2026-11-16", 1, 2, "200.00"),
    ]


def test_rows_by_month(shop, boss):
    placed_on(shop, (2026, 10, 30, 12), (2026, 11, 2, 12))

    response = boss.get(
        f"{SALES}?date_from=2026-10-30&date_to=2026-11-02&group=month",
    )

    assert rows(response) == [
        ("2026-10-01", 1, 2, "200.00"),
        ("2026-11-01", 1, 2, "200.00"),
    ]


def test_a_return_comes_off_the_day_the_order_was_placed(delivered, staff, boss):
    Order.objects.filter(number=delivered["number"]).update(
        date_placed=datetime(2026, 11, 16, 12, tzinfo=ZoneInfo("Europe/Istanbul")),
    )
    receive(staff, delivered, quantity=1)

    response = boss.get(f"{SALES}?date_from=2026-11-16&date_to=2026-11-16")

    assert rows(response) == [("2026-11-16", 1, 1, "100.00")]


def test_the_rows_add_up_to_the_totals(shop, boss):
    placed_on(shop, (2026, 11, 15, 12), (2026, 11, 16, 9))

    body = boss.get(f"{SALES}?date_from=2026-11-15&date_to=2026-11-17").json()

    assert sum(r["orders"] for r in body["rows"]) == body["totals"]["orders"]
    assert sum(r["items"] for r in body["rows"]) == body["totals"]["items"]


def test_an_unknown_grouping_is_refused(boss):
    assert (
        boss.get(f"{SALES}?group=year").status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    )


# --- Products -----------------------------------------------------------


def buy(variant, quantity=2):
    """A COD order for `quantity` units of one variant."""
    return place_units(
        variant,
        quantity=quantity,
        payment_method="cod",
        cod_fee_consent=True,
    )


@pytest.fixture
def two_products(shop):
    """Elbise (100.00) and Pantolon (50.00), each with stock to spare."""
    elbise, variants = shop
    colour = variants["M"].variant_profile.colour
    pantolon = catalog.create_product(
        title="Pantolon",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=elbise.categories.get(),
        shelf_price="50.00",
    )
    trousers = catalog.create_variant(pantolon, colour=colour, size="M", stock=50)
    ProductProfile.objects.filter(product=pantolon).update(
        status=ProductStatus.PUBLISHED,
    )
    StockRecord.objects.filter(product=variants["M"]).update(num_in_stock=50)
    return elbise, variants, pantolon, trousers


def ranked(response):
    return [(r["title"], r["units"], r["revenue"]) for r in response.json()["rows"]]


def test_products_rank_by_revenue_by_default(two_products, boss):
    _, variants, _, trousers = two_products
    buy(variants["M"])  # 2 x 100
    buy(trousers, 5)  # 5 x 50
    buy(trousers, 1)  # 1 x 50

    # Pantolon: 6 units, 300.00. Elbise: 2 units, 200.00.
    assert ranked(boss.get(PRODUCTS)) == [
        ("Pantolon", 6, "300.00"),
        ("Elbise", 2, "200.00"),
    ]


def test_products_can_rank_by_units(two_products, boss):
    _, variants, _, trousers = two_products
    buy(variants["M"], 3)
    buy(trousers, 4)

    # By revenue Elbise leads (300.00 against 200.00); by units Pantolon (4 against 3).
    assert [r[0] for r in ranked(boss.get(PRODUCTS))] == ["Elbise", "Pantolon"]
    assert [r[0] for r in ranked(boss.get(f"{PRODUCTS}?sort=units"))] == [
        "Pantolon",
        "Elbise",
    ]


def test_the_variants_of_one_product_share_a_row(two_products, boss):
    _, variants, _, _ = two_products
    StockRecord.objects.filter(product=variants["L"]).update(num_in_stock=50)
    buy(variants["M"], 2)
    buy(variants["L"], 3)

    assert ranked(boss.get(PRODUCTS)) == [("Elbise", 5, "500.00")]


def test_products_net_out_received_returns(delivered, staff, boss):
    receive(staff, delivered, quantity=1)

    assert ranked(boss.get(PRODUCTS)) == [("Elbise", 1, "100.00")]


def test_a_cancelled_order_is_not_ranked(two_products, boss):
    _, variants, _, _ = two_products
    number = buy(variants["M"])["number"]
    act(boss, number, "cancel", {"reason": "Müşteri vazgeçti"})

    assert ranked(boss.get(PRODUCTS)) == []


def test_products_follow_the_kdv_choice(two_products, boss):
    _, variants, _, _ = two_products
    buy(variants["M"])

    # 200.00 at 10% KDV is 181.82 without it.
    assert ranked(boss.get(f"{PRODUCTS}?kdv=excl")) == [("Elbise", 2, "181.82")]


def test_each_product_carries_the_previous_periods_figures(two_products, boss):
    _, variants, _, trousers = two_products
    last_month = buy(variants["M"], 3)["number"]
    buy(variants["M"], 2)
    buy(trousers, 1)
    Order.objects.filter(number=last_month).update(
        date_placed=timezone.now() - timedelta(days=40),
    )

    rows = boss.get(PRODUCTS).json()["rows"]
    by_title = {r["title"]: r for r in rows}

    # The last 30 days against the 30 before: Elbise sold 2 units now, 3 then.
    assert (by_title["Elbise"]["units"], by_title["Elbise"]["previous_units"]) == (2, 3)
    assert by_title["Elbise"]["previous_revenue"] == "300.00"
    # Pantolon was not sold before: zeros, not missing.
    assert by_title["Pantolon"]["previous_units"] == 0
    assert by_title["Pantolon"]["previous_revenue"] == "0.00"


# --- CSV ----------------------------------------------------------------

SALES_CSV = f"{SALES}.csv"
PRODUCTS_CSV = f"{PRODUCTS}.csv"


def csv_rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


@pytest.mark.parametrize("url", [SALES_CSV, PRODUCTS_CSV])
def test_staff_cannot_download_a_report(url, staff):
    assert staff.get(url).status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("url", [SALES_CSV, PRODUCTS_CSV])
def test_anonymous_cannot_download_a_report(url, client):
    assert client.get(url).status_code == HTTPStatus.UNAUTHORIZED


def test_the_sales_csv_has_the_rows_of_the_report(shop, boss):
    placed_on(shop, (2026, 11, 15, 12), (2026, 11, 16, 9))

    response = boss.get(f"{SALES_CSV}?date_from=2026-11-15&date_to=2026-11-17")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    assert csv_rows(response) == [
        {
            "period": "2026-11-15",
            "orders": "1",
            "items": "2",
            "revenue": "200.00",
            "average_order": "200.00",
        },
        {
            "period": "2026-11-16",
            "orders": "1",
            "items": "2",
            "revenue": "200.00",
            "average_order": "200.00",
        },
        {
            "period": "2026-11-17",
            "orders": "0",
            "items": "0",
            "revenue": "0.00",
            "average_order": "0.00",
        },
    ]


def test_the_products_csv_has_the_ranking(two_products, boss):
    _, variants, _, trousers = two_products
    buy(variants["M"], 2)
    buy(trousers, 6)

    response = boss.get(PRODUCTS_CSV)

    assert response.status_code == HTTPStatus.OK, response.content
    assert csv_rows(response) == [
        {
            "product_id": str(trousers.parent_id),
            "title": "Pantolon",
            "units": "6",
            "revenue": "300.00",
            "previous_units": "0",
            "previous_revenue": "0.00",
        },
        {
            "product_id": str(variants["M"].parent_id),
            "title": "Elbise",
            "units": "2",
            "revenue": "200.00",
            "previous_units": "0",
            "previous_revenue": "0.00",
        },
    ]


def test_a_title_that_looks_like_a_formula_is_defanged(shop, boss):
    shop[0].title = '=HYPERLINK("http://evil")'
    shop[0].save()
    buy(shop[1]["M"])

    row = csv_rows(boss.get(PRODUCTS_CSV))[0]

    assert row["title"].startswith("'=")


def test_each_download_is_written_to_the_audit_log(shop, boss, owner):
    placed_on(shop, (2026, 11, 15, 12))

    boss.get(f"{SALES_CSV}?date_from=2026-11-15&date_to=2026-11-17")
    boss.get(f"{PRODUCTS_CSV}?preset=7d")

    entries = AuditEntry.objects.filter(action=audit.Action.EXPORT_RUN)
    assert sorted(e.target_type for e in entries) == [
        "reports_products_csv",
        "reports_sales_csv",
    ]
    assert {e.actor_email for e in entries} == {owner.email}
    sales = entries.get(target_type="reports_sales_csv")
    assert sales.after["from"] == "2026-11-15"
    assert sales.after["to"] == "2026-11-17"


def test_a_json_report_is_not_audited(shop, boss):
    boss.get(SALES)
    boss.get(PRODUCTS)

    assert not AuditEntry.objects.filter(action=audit.Action.EXPORT_RUN).exists()


# --- short cache -----------------------------------------------------


def test_a_report_asked_again_within_the_minute_is_served_from_the_cache(
    two_products,
    boss,
):
    _, variants, _, _ = two_products
    buy(variants["M"])
    assert boss.get(SALES).json()["totals"]["orders"] == 1

    buy(variants["M"])

    assert boss.get(SALES).json()["totals"]["orders"] == 1  # still the cached one


def test_the_cache_is_per_query(two_products, boss):
    _, variants, _, _ = two_products
    buy(variants["M"])
    boss.get(SALES)

    excl = boss.get(f"{SALES}?kdv=excl").json()
    seven = boss.get(f"{SALES}?preset=7d").json()

    assert excl["totals"]["revenue"] == "181.82"
    assert seven["range"]["start"] != boss.get(SALES).json()["range"]["start"]


def test_the_products_report_is_cached_too(two_products, boss):
    _, variants, _, _ = two_products
    buy(variants["M"])
    assert ranked(boss.get(PRODUCTS)) == [("Elbise", 2, "200.00")]

    buy(variants["M"])

    assert ranked(boss.get(PRODUCTS)) == [("Elbise", 2, "200.00")]


def test_a_csv_is_always_fresh_and_never_cached(two_products, boss):
    _, variants, _, _ = two_products
    buy(variants["M"])
    boss.get(PRODUCTS)  # fills the cache

    buy(variants["M"])

    assert [r["units"] for r in csv_rows(boss.get(PRODUCTS_CSV))] == ["4"]


def test_a_staff_member_never_gets_a_cached_report(two_products, boss, staff):
    _, variants, _, _ = two_products
    buy(variants["M"])
    boss.get(SALES)  # fills the cache

    assert staff.get(SALES).status_code == HTTPStatus.FORBIDDEN


def test_the_home_summary_shows_the_reports_own_figures(cod, boss):
    summary = boss.get("/api/admin/home").json()["report_summary"]
    assert [p["preset"] for p in summary["periods"]] == ["today", "7d", "30d"]
    for row in summary["periods"]:
        report = boss.get(f"{SALES}?preset={row['preset']}").json()["totals"]
        assert (row["orders"], row["revenue"]) == (report["orders"], report["revenue"])
    assert summary["periods"][0]["orders"] == 1
