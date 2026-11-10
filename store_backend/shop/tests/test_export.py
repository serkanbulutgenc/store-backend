# ruff: noqa: PLR2004, F401, F811
import csv
import io
from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.services import accountant_export
from store_backend.services import audit
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import OrderLineSnapshot
from store_backend.shop.models import StoreSettings
from store_backend.shop.tests.test_invoicing import issue
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import cod
from store_backend.shop.tests.test_orders import place
from store_backend.shop.tests.test_orders import staff
from store_backend.shop.tests.test_orders import transfer
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

URL = "/api/admin/exports/accountant.csv"


def fetch(api, start=None, end=None):
    today = timezone.localdate()
    return api.get(f"{URL}?date_from={start or today}&date_to={end or today}")


def rows_of(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


def test_staff_get_403(transfer, staff):
    assert fetch(staff).status_code == HTTPStatus.FORBIDDEN


def test_the_owner_downloads_a_csv(transfer, boss):
    response = fetch(boss)

    assert response.status_code == HTTPStatus.OK, response.content
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    rows = rows_of(response)
    assert {r["order_number"] for r in rows} == {transfer}


def test_totals_equal_the_order_snapshots(shop, boss):
    StoreSettings.objects.update_or_create(pk=1, defaults={"cod_fee": "15.00"})
    number = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    order = Order.objects.get(number=number)

    rows = rows_of(fetch(boss))

    assert [r["line_kind"] for r in rows] == ["product", "cod"]
    snaps = [
        *OrderLineSnapshot.objects.filter(line__order=order),
        *OrderFeeSnapshot.objects.filter(order=order),
    ]
    assert sum(Decimal(r["amount_excl"]) for r in rows) == sum(
        s.total_excl for s in snaps
    )
    assert sum(Decimal(r["kdv_amount"]) for r in rows) == sum(
        s.total_kdv for s in snaps
    )
    assert sum(Decimal(r["amount_incl"]) for r in rows) == order.total_incl_tax
    assert {r["order_total_incl"] for r in rows} == {str(order.total_incl_tax)}
    assert rows[0]["payment_method"] == "cod"


def test_the_csv_carries_the_invoice_but_no_contact_details(transfer, staff, boss):
    issue(staff, transfer)
    order = Order.objects.get(number=transfer)

    response = fetch(boss)

    rows = rows_of(response)
    assert rows[0]["invoice_number"] == "AUR2026000001"
    assert rows[0]["invoice_status"] == "issued"
    assert set(rows[0]) == set(accountant_export.COLUMNS)
    text = response.content.decode()
    assert order.guest_email not in text
    assert str(order.shipping_address.phone_number) not in text
    assert not any("email" in c or "phone" in c or "note" in c for c in rows[0])


def test_the_range_is_by_placement_day_and_inclusive(transfer, cod, boss):
    today = timezone.localdate()
    Order.objects.filter(number=cod).update(
        date_placed=timezone.now() - timedelta(days=5),
    )

    only_today = {r["order_number"] for r in rows_of(fetch(boss))}
    both = {r["order_number"] for r in rows_of(fetch(boss, today - timedelta(days=5)))}

    assert only_today == {transfer}
    assert both == {transfer, cod}


def test_an_empty_range_is_just_the_header(boss):
    response = fetch(boss)

    assert response.status_code == HTTPStatus.OK
    assert response.content.decode().strip() == ",".join(accountant_export.COLUMNS)


def test_a_backwards_or_oversized_range_is_rejected(boss):
    today = timezone.localdate()

    assert fetch(boss, today, today - timedelta(days=1)).status_code == 422
    assert fetch(boss, today - timedelta(days=400), today).status_code == 422


def test_each_export_is_audited_with_range_and_user(transfer, boss, owner):
    fetch(boss)

    entry = AuditEntry.objects.get(action=audit.Action.EXPORT_RUN)
    today = str(timezone.localdate())
    assert entry.actor_email == owner.email
    assert entry.after == {"from": today, "to": today, "orders": 1}


def test_cells_that_look_like_formulas_are_defanged():
    assert accountant_export.safe("=SUM(A1)") == "'=SUM(A1)"
    assert accountant_export.safe("Ayşe") == "Ayşe"
    assert accountant_export.safe(None) == ""
