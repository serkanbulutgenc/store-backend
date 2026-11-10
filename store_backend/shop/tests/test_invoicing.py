# ruff: noqa: PLR2004, F401, F811
from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.shop.models import StoreSettings
from store_backend.shop.tests.test_orders import audit_actions
from store_backend.shop.tests.test_orders import boss
from store_backend.shop.tests.test_orders import cod
from store_backend.shop.tests.test_orders import pay
from store_backend.shop.tests.test_orders import place
from store_backend.shop.tests.test_orders import staff
from store_backend.shop.tests.test_orders import transfer
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

ORDERS = "/api/admin/orders"
HOME = "/api/admin/home"


def act(api, number, action, data=None):
    return api.post(f"{ORDERS}/{number}/{action}", data or {})


def ship_it(api, number):
    pay(api, number)
    response = act(api, number, "ship", {"carrier": "Yurtiçi", "tracking_number": "1"})
    assert response.status_code == HTTPStatus.OK, response.content


def put_invoice(api, order, /, **fields):
    return api.put(f"{ORDERS}/{order}/invoice", fields)


def issue(api, order, *, on=None, invoice_number="AUR2026000001"):
    return put_invoice(
        api,
        order,
        status="issued",
        number=invoice_number,
        date=str(on or timezone.localdate()),
    )


def home(api):
    response = api.get(HOME)
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


def numbers(rows):
    return [row["number"] for row in rows]


# --- the record -----------------------------------------------------------------


def test_an_order_starts_without_an_invoice(transfer, staff):
    body = staff.get(f"{ORDERS}/{transfer}").json()

    assert body["invoice"]["status"] == "not_issued"
    assert body["invoice"]["number"] == ""


def test_staff_record_an_issued_invoice(transfer, staff):
    response = issue(staff, transfer, on="2026-11-09")

    assert response.status_code == HTTPStatus.OK, response.content
    invoice = response.json()["invoice"]
    assert invoice["status"] == "issued"
    assert invoice["number"] == "AUR2026000001"
    assert invoice["date"] == "2026-11-09"
    assert invoice["cancel_by"] == "2026-11-17"  # 8 days after the invoice date
    assert audit_actions(transfer)[-1] == audit.Action.INVOICE_EDIT


def test_an_issued_invoice_needs_a_number_and_date(transfer, staff):
    response = put_invoice(staff, transfer, status="issued")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert set(response.json()["fields"]) == {"number", "date"}


def test_the_invoice_date_cannot_be_in_the_future(transfer, staff):
    tomorrow = timezone.localdate() + timedelta(days=1)

    response = issue(staff, transfer, on=tomorrow)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "date" in response.json()["fields"]


def test_a_credit_note_needs_both_its_number_and_date(transfer, staff):
    response = put_invoice(
        staff,
        transfer,
        status="issued",
        number="A1",
        date=str(timezone.localdate()),
        credit_note_number="C1",
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "credit_note_date" in response.json()["fields"]


def test_a_credit_note_cannot_predate_the_invoice(transfer, staff):
    today = timezone.localdate()

    response = put_invoice(
        staff,
        transfer,
        status="issued",
        number="A1",
        date=str(today),
        credit_note_number="C1",
        credit_note_date=str(today - timedelta(days=1)),
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_not_issued_cannot_carry_invoice_fields(transfer, staff):
    response = put_invoice(staff, transfer, status="not_issued", number="A1")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_record_can_be_reset_to_not_issued(transfer, staff):
    issue(staff, transfer)

    response = put_invoice(staff, transfer, status="not_issued")

    assert response.json()["invoice"]["status"] == "not_issued"
    assert response.json()["invoice"]["number"] == ""


def test_an_invoice_number_is_used_once(transfer, cod, staff):
    issue(staff, transfer)

    response = issue(staff, cod)

    assert response.status_code == HTTPStatus.CONFLICT


def test_invoice_edits_are_audited_with_before_and_after(transfer, staff):
    issue(staff, transfer)

    entry = AuditEntry.objects.filter(action=audit.Action.INVOICE_EDIT).get()

    assert entry.before["status"] == "not_issued"
    assert entry.after["number"] == "AUR2026000001"


# --- the invoice sheet ------------------------------------------------------------


def test_the_sheet_carries_what_the_portal_needs(transfer, staff):
    staff.patch(
        f"{ORDERS}/{transfer}/invoice-details",
        {"buyer_tax_number": "12345678901", "company_title": "Örnek Ltd"},
    )

    response = staff.get(f"{ORDERS}/{transfer}/invoice-sheet")

    assert response.status_code == HTTPStatus.OK, response.content
    sheet = response.json()
    assert sheet["order_number"] == transfer
    assert sheet["buyer"]["name"]
    assert sheet["buyer"]["tax_number"] == "12345678901"
    assert sheet["buyer"]["company_title"] == "Örnek Ltd"
    assert sheet["buyer"]["il"]
    line = sheet["lines"][0]
    assert line["quantity"] == 2
    assert line["kdv_rate"] == "10.00"
    assert line["unit_price_excl"] == "90.91"
    assert line["total_excl"] == "181.82"
    assert line["total_kdv"] == "18.18"
    assert line["total_incl"] == "200.00"
    assert sheet["total_excl"] == "181.82"
    assert sheet["total_kdv"] == "18.18"
    assert sheet["total_incl"] == "200.00"


def test_the_sheet_lists_the_cod_fee_as_its_own_line(shop, staff):
    StoreSettings.objects.update_or_create(pk=1, defaults={"cod_fee": "15.00"})
    cod = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    sheet = staff.get(f"{ORDERS}/{cod}/invoice-sheet").json()

    kinds = [line["kind"] for line in sheet["lines"]]
    assert kinds[0] == "product"
    assert "cod" in kinds
    total = sum(Decimal(line["total_incl"]) for line in sheet["lines"])
    assert total == Decimal(sheet["total_incl"])


# --- Home flags -------------------------------------------------------------------


def test_marking_shipped_surfaces_the_order_without_an_invoice(transfer, staff):
    assert numbers(home(staff)["shipped_without_invoice"]["rows"]) == []

    ship_it(staff, transfer)

    flagged = home(staff)["shipped_without_invoice"]
    assert numbers(flagged["rows"]) == [transfer]
    assert flagged["count"] == 1


def test_recording_the_invoice_clears_the_shipped_flag(transfer, staff):
    ship_it(staff, transfer)

    issue(staff, transfer)

    assert numbers(home(staff)["shipped_without_invoice"]["rows"]) == []


def test_an_unshipped_order_is_not_flagged_for_an_invoice(transfer, staff):
    pay(staff, transfer)

    assert numbers(home(staff)["shipped_without_invoice"]["rows"]) == []


def test_cancelling_an_invoiced_order_raises_the_credit_note_flag(
    transfer,
    staff,
    boss,
):
    pay(staff, transfer)
    issue(staff, transfer)
    assert numbers(home(staff)["invoice_attention"]["rows"]) == []

    response = act(boss, transfer, "cancel", {"reason": "Müşteri vazgeçti"})
    assert response.status_code == HTTPStatus.OK, response.content

    rows = home(staff)["invoice_attention"]["rows"]
    assert numbers(rows) == [transfer]
    assert rows[0]["invoice_number"] == "AUR2026000001"
    assert rows[0]["needed"] == "cancel_invoice"  # still inside the 8 days


def test_after_eight_days_the_flag_asks_for_a_credit_note(transfer, staff, boss):
    pay(staff, transfer)
    issue(staff, transfer, on=timezone.localdate() - timedelta(days=9))
    act(boss, transfer, "cancel", {"reason": "Müşteri vazgeçti"})

    rows = home(staff)["invoice_attention"]["rows"]

    assert rows[0]["needed"] == "credit_note"


def test_recording_the_credit_note_clears_the_flag(transfer, staff, boss):
    pay(staff, transfer)
    today = timezone.localdate()
    issue(staff, transfer, on=today - timedelta(days=9))
    act(boss, transfer, "cancel", {"reason": "Müşteri vazgeçti"})

    put_invoice(
        staff,
        transfer,
        status="issued",
        number="AUR2026000001",
        date=str(today - timedelta(days=9)),
        credit_note_number="IAD2026000001",
        credit_note_date=str(today),
    )

    assert numbers(home(staff)["invoice_attention"]["rows"]) == []


def test_cancelling_the_invoice_itself_clears_the_flag(transfer, staff, boss):
    pay(staff, transfer)
    issue(staff, transfer)
    act(boss, transfer, "cancel", {"reason": "Müşteri vazgeçti"})

    put_invoice(
        staff,
        transfer,
        status="cancelled",
        number="AUR2026000001",
        date=str(timezone.localdate()),
    )

    assert numbers(home(staff)["invoice_attention"]["rows"]) == []


def test_a_returned_invoiced_order_needs_a_credit_note_too(transfer, staff):
    ship_it(staff, transfer)
    issue(staff, transfer, on=timezone.localdate() - timedelta(days=20))
    act(staff, transfer, "deliver")
    act(staff, transfer, "request-return")
    act(staff, transfer, "mark-returned")

    rows = home(staff)["invoice_attention"]["rows"]

    assert numbers(rows) == [transfer]
    assert rows[0]["needed"] == "credit_note"


# --- the other Home lists -----------------------------------------------------------


def test_new_orders_are_listed_newest_first(shop, transfer, staff):
    later = place(shop[1]["M"])

    new = home(staff)["new_orders"]

    assert new["count"] == 2
    assert numbers(new["rows"]) == [later, transfer]


def test_a_paid_order_leaves_the_new_list(transfer, staff):
    pay(staff, transfer)

    assert numbers(home(staff)["new_orders"]["rows"]) == []


def test_unpaid_transfers_appear_in_the_last_twelve_hours_of_the_window(
    transfer,
    cod,
    staff,
):
    assert numbers(home(staff)["unpaid_transfers_near_expiry"]["rows"]) == []

    Order.objects.filter(number__in=[transfer, cod]).update(
        date_placed=timezone.now() - timedelta(hours=40),
    )

    rows = home(staff)["unpaid_transfers_near_expiry"]["rows"]
    assert numbers(rows) == [transfer]  # COD has no payment deadline
    assert rows[0]["expires_at"]


def test_low_stock_lists_variants_at_or_below_the_threshold(shop, staff):
    low = home(staff)["low_stock"]

    skus = {row["size"] for row in low["rows"]}
    assert "L" in skus  # the fixture's sold-out size
    assert low["count"] == len(low["rows"])


def test_published_products_without_photos_are_listed(shop, staff):
    product = shop[0]

    rows = home(staff)["products_without_photos"]["rows"]

    assert [row["title"] for row in rows] == [product.title]
    product.profile.status = ProductStatus.DRAFT
    product.profile.save()
    assert home(staff)["products_without_photos"]["rows"] == []


def test_each_list_is_capped_but_counts_everything(shop, transfer, staff, monkeypatch):
    place(shop[1]["M"])
    monkeypatch.setattr("store_backend.services.home.ROW_LIMIT", 1)

    new = home(staff)["new_orders"]

    assert new["count"] == 2
    assert len(new["rows"]) == 1


# --- permissions ---------------------------------------------------------------------


def test_the_invoice_endpoints_need_a_signed_in_staff_member(client, transfer):
    assert client.get(HOME).status_code == HTTPStatus.UNAUTHORIZED
    assert client.put(f"{ORDERS}/{transfer}/invoice").status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.METHOD_NOT_ALLOWED,
    )
