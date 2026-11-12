# ruff: noqa: PLR2004
import re
from decimal import Decimal
from http import HTTPStatus

import pytest
from oscar.apps.order.models import Order
from oscar.apps.partner.models import StockRecord

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import shop_settings
from store_backend.shop.models import ConsentRecord
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import OrderLineSnapshot
from store_backend.shop.models import PreInformationSnapshot
from store_backend.shop.tests.conftest import Guest

pytestmark = pytest.mark.django_db

LINES = "/api/store/basket/lines"
CHECKOUT = "/api/store/checkout"


def payload(**overrides):
    body = {
        "full_name": "Ayşe Yılmaz",
        "email": "ayse@example.com",
        "phone": "0532 123 45 67",
        "shipping_address": {
            "il": "Mersin",
            "ilce": "Yenişehir",
            "mahalle": "Çiftlikköy Mah.",
            "street": "Gül Sok. No:5 D:3",
            "postal_code": "33110",
        },
        "payment_method": "bank_transfer",
        "contract_acknowledged": True,
    }
    body.update(overrides)
    return body


def fill(guest, variant, quantity=2):
    response = guest.post(LINES, {"variant_id": variant.pk, "quantity": quantity})
    assert response.status_code == HTTPStatus.OK, response.content


def stock(variant) -> StockRecord:
    return StockRecord.objects.get(product=variant)


def money(amount) -> str:
    """Turkish money text: 1.234,50 TL."""
    whole, _, cents = f"{Decimal(amount):.2f}".partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return ".".join([whole, *groups]) + f",{cents} TL"


def set_fees(owner, **changes):
    shop_settings.update(owner, **changes)


@pytest.fixture
def guest(shop):
    _, v = shop
    g = Guest()
    fill(g, v["M"])
    return g


# --- placement ---------------------------------------------------------------


def test_guest_places_a_bank_transfer_order(guest, shop, owner):
    _, v = shop
    set_fees(
        owner,
        shipping_fee="30.00",
        bank_name="Ziraat",
        bank_account_holder="Aura Tekstil",
        bank_iban="TR330006100519786457841326",
    )

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED, response.content
    body = response.json()
    assert re.fullmatch(r"AT-[A-Z0-9]{6}", body["number"])
    assert body["status"] == "new"
    assert body["payment_status"] == "unpaid"
    assert body["payment_method"] == "bank_transfer"
    assert body["items_total"] == "200.00"
    assert body["shipping_fee"] == "30.00"
    assert body["cod_fee"] == "0.00"
    assert body["total"] == "230.00"
    assert body["bank"] == {
        "name": "Ziraat",
        "account_holder": "Aura Tekstil",
        "iban": "TR330006100519786457841326",
        "reference": body["number"],
    }
    assert [(line["sku"], line["quantity"]) for line in body["lines"]] == [
        (v["M"].variant_profile.sku, 2),
    ]

    order = Order.objects.get(number=body["number"])
    assert order.user is None
    assert order.guest_email == "ayse@example.com"
    assert order.status == "new"
    assert order.total_incl_tax == Decimal("230.00")
    assert order.shipping_incl_tax == Decimal("30.00")
    assert order.shipping_address.phone_number.as_e164 == "+905321234567"
    assert order.shipping_address.state == "Mersin"
    assert order.shipping_address.line4 == "Yenişehir"
    assert order.shipping_address.postcode == "33110"
    # Billing is a copy of shipping unless another address is given.
    assert order.billing_address.line1 == order.shipping_address.line1
    assert order.sources.get().source_type.code == "bank_transfer"
    assert order.sources.get().amount_debited == 0


def test_the_order_number_is_not_the_basket_id(guest):
    numbers = {guest.post(CHECKOUT, payload()).json()["number"]}
    assert not any(n.endswith("100001") for n in numbers)


def test_stock_is_reserved_and_the_basket_emptied(guest, shop):
    _, v = shop

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED
    record = stock(v["M"])
    assert (record.num_in_stock, record.num_allocated) == (5, 2)
    assert guest.get("/api/store/basket").json()["item_count"] == 0


def test_cod_goes_straight_to_preparing_with_the_fee(guest, owner):
    set_fees(owner, shipping_fee="30.00", cod_fee="15.00")

    response = guest.post(
        "/api/store/checkout",
        payload(payment_method="cod", cod_fee_consent=True),
    )

    assert response.status_code == HTTPStatus.CREATED, response.content
    body = response.json()
    assert body["status"] == "preparing"
    assert body["cod_fee"] == "15.00"
    assert body["total"] == "245.00"
    assert body["bank"] is None
    order = Order.objects.get(number=body["number"])
    assert order.total_incl_tax == Decimal("245.00")
    assert order.sources.get().source_type.code == "cod"


def test_free_shipping_at_the_threshold(guest, owner):
    set_fees(owner, shipping_fee="30.00", free_shipping_threshold="200.00")

    body = guest.post(CHECKOUT, payload()).json()

    assert body["shipping_fee"] == "0.00"
    assert body["total"] == "200.00"


def test_a_signed_in_customer_owns_the_order(shop, customer, log_in):
    from django.test import Client  # noqa: PLC0415

    _, v = shop
    shopper = Guest(log_in(Client(), customer))
    fill(shopper, v["M"], 1)

    response = shopper.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED, response.content
    order = Order.objects.get(number=response.json()["number"])
    assert order.user == customer


def test_billing_can_differ_from_shipping(guest):
    billing = {
        "il": "İstanbul",
        "ilce": "Kadıköy",
        "mahalle": "Caferağa Mah.",
        "street": "Moda Cad. No:1",
        "postal_code": "",
    }

    body = guest.post(CHECKOUT, payload(billing_address=billing)).json()

    order = Order.objects.get(number=body["number"])
    assert order.billing_address.state == "İstanbul"
    assert order.shipping_address.state == "Mersin"


# --- validation ---------------------------------------------------------------


def test_the_contract_acknowledgement_is_required(guest, shop):
    _, v = shop

    for value in (False, None):
        response = guest.post(CHECKOUT, payload(contract_acknowledged=value))
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "contract_acknowledged" in response.json()["fields"]

    assert not Order.objects.exists()
    assert stock(v["M"]).num_allocated in (0, None)


def test_the_contract_acknowledgement_cannot_be_omitted(guest):
    body = payload()
    del body["contract_acknowledged"]

    response = guest.post(CHECKOUT, body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not Order.objects.exists()


def test_cod_needs_the_fee_consent_even_when_the_fee_is_zero(guest):
    response = guest.post(CHECKOUT, payload(payment_method="cod"))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "cod_fee_consent" in response.json()["fields"]
    assert not Order.objects.exists()


def test_bank_transfer_does_not_need_the_fee_consent(guest):
    response = guest.post(CHECKOUT, payload(cod_fee_consent=False))

    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", ""),
        ("full_name", "Ayşe"),
        ("email", "not-an-email"),
        ("phone", ""),
        ("phone", "12345"),
        ("payment_method", "card"),
    ],
)
def test_mandatory_fields_are_validated(guest, field, value):
    response = guest.post(CHECKOUT, payload(**{field: value}))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.content
    assert field in response.json()["fields"]
    assert not Order.objects.exists()


@pytest.mark.parametrize("missing", ["il", "ilce", "mahalle", "street"])
def test_the_shipping_address_parts_are_mandatory(guest, missing):
    body = payload()
    body["shipping_address"][missing] = " "

    response = guest.post(CHECKOUT, body)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert f"shipping_address.{missing}" in response.json()["fields"]


def test_the_postal_code_is_optional(guest):
    body = payload()
    del body["shipping_address"]["postal_code"]

    assert guest.post(CHECKOUT, body).status_code == HTTPStatus.CREATED


def test_an_empty_basket_is_refused(shop):
    response = Guest().post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == "basket_empty"


def test_placing_twice_does_not_create_a_second_order(guest):
    assert guest.post(CHECKOUT, payload()).status_code == HTTPStatus.CREATED

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert Order.objects.count() == 1


# --- records ------------------------------------------------------------------


def test_the_consent_record_keeps_versions_time_ip_and_agent(guest, owner):
    shop_settings.update(owner, legal_versions={"distance_sales_contract": "3"})

    body = guest.post(
        CHECKOUT,
        payload(payment_method="cod", cod_fee_consent=True, marketing_consent=True),
    ).json()

    consent = ConsentRecord.objects.get(order__number=body["number"])
    assert consent.contract_acknowledged is True
    assert consent.cod_fee_consent is True
    assert consent.marketing_consent_at is not None
    assert consent.legal_versions["distance_sales_contract"] == "3"
    assert consent.legal_versions["privacy_notice"] == "1"
    assert consent.ip_address == "203.0.113.7"
    assert consent.user_agent == "TestBrowser/1.0"
    assert consent.created_at is not None


def test_marketing_consent_is_optional(guest):
    body = guest.post(CHECKOUT, payload()).json()

    consent = ConsentRecord.objects.get(order__number=body["number"])
    assert consent.marketing_consent_at is None
    assert consent.cod_fee_consent is False


def test_the_forwarded_address_is_used_only_when_trusted(guest, settings):
    settings.TRUST_FORWARDED_FOR = True
    guest.client.defaults["HTTP_X_FORWARDED_FOR"] = "198.51.100.9, 10.0.0.1"

    body = guest.post(CHECKOUT, payload()).json()

    consent = ConsentRecord.objects.get(order__number=body["number"])
    assert consent.ip_address == "198.51.100.9"


def test_line_snapshots_freeze_the_kdv_split_and_sum_to_the_order(guest, owner):
    set_fees(owner, shipping_fee="30.00", cod_fee="15.00", fee_kdv_rate="20.00")

    body = guest.post(
        CHECKOUT,
        payload(payment_method="cod", cod_fee_consent=True),
    ).json()

    order = Order.objects.get(number=body["number"])
    line = order.lines.get()
    snap = OrderLineSnapshot.objects.get(line=line)
    assert snap.unit_price_incl == Decimal("100.00")
    assert snap.kdv_rate == catalog.kdv_rate_for(line.product)
    assert snap.total_excl + snap.total_kdv == snap.total_incl == Decimal("200.00")
    assert line.line_price_incl_tax == snap.total_incl
    assert line.line_price_excl_tax == snap.total_excl
    fees = {f.kind: f for f in OrderFeeSnapshot.objects.filter(order=order)}
    assert set(fees) == {"shipping", "cod"}
    assert fees["shipping"].total_incl == Decimal("30.00")
    assert fees["cod"].total_incl == Decimal("15.00")
    assert fees["cod"].total_excl + fees["cod"].total_kdv == Decimal("15.00")
    parts = [snap, *fees.values()]
    assert sum(p.total_incl for p in parts) == order.total_incl_tax
    assert sum(p.total_excl for p in parts) == order.total_excl_tax


def test_later_price_changes_do_not_touch_the_order(guest, shop, owner):
    product, _ = shop
    body = guest.post(CHECKOUT, payload()).json()

    ProductProfile.objects.filter(product=product).update(shelf_price="999.00")

    order = Order.objects.get(number=body["number"])
    assert order.total_incl_tax == Decimal("200.00")
    assert OrderLineSnapshot.objects.get().unit_price_incl == Decimal("100.00")


def test_the_pre_information_text_matches_the_order_totals(guest, owner):
    set_fees(
        owner,
        shipping_fee="30.00",
        cod_fee="15.00",
        seller_name="Aura Tekstil",
        seller_tax_number="1234567890",
    )

    body = guest.post(
        CHECKOUT,
        payload(payment_method="cod", cod_fee_consent=True),
    ).json()

    order = Order.objects.get(number=body["number"])
    text = PreInformationSnapshot.objects.get(order=order).text
    assert order.number in text
    assert "Elbise" in text
    assert "Siyah" in text
    assert "Ayşe Yılmaz" in text
    assert "Aura Tekstil" in text
    assert "Çiftlikköy Mah." in text
    assert money(order.total_incl_tax) in text  # 245,00 TL
    assert money("200.00") in text
    assert money("30.00") in text
    assert money("15.00") in text
    assert "Kapıda ödeme" in text
    assert "14 gün" in text  # withdrawal summary


def test_the_pre_information_text_flags_hygiene_products(guest, shop):
    product, _ = shop
    from store_backend.catalog.models import ProductClassProfile  # noqa: PLC0415

    ProductClassProfile.objects.filter(
        product_class=product.product_class,
    ).update(is_hygiene=True)

    body = guest.post(CHECKOUT, payload()).json()

    text = PreInformationSnapshot.objects.get(order__number=body["number"]).text
    assert "açıldıktan sonra iade edilemez" in text


# --- abuse protection ---------------------------------------------------------


def test_a_filled_honeypot_is_rejected_without_placing(guest, shop):
    _, v = shop

    response = guest.post(CHECKOUT, payload(website="https://spam.example"))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert not Order.objects.exists()
    assert stock(v["M"]).num_allocated in (0, None)


def test_checkout_is_rate_limited_per_ip(guest, settings):
    settings.CHECKOUT_RATE_LIMIT = (3, 600)
    bad = payload(contract_acknowledged=False)

    codes = [guest.post(CHECKOUT, bad).status_code for _ in range(4)]

    assert codes == [422, 422, 422, 429]
    assert guest.post(CHECKOUT, bad).json()["code"] == "rate_limited"


def test_the_rate_limit_is_per_ip(guest, shop, settings):
    _, v = shop
    settings.CHECKOUT_RATE_LIMIT = (1, 600)
    bad = payload(contract_acknowledged=False)
    guest.post(CHECKOUT, bad)
    other = Guest(ip="203.0.113.99")
    fill(other, v["M"], 1)

    assert guest.post(CHECKOUT, bad).status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert other.post(CHECKOUT, payload()).status_code == HTTPStatus.CREATED


# --- stock conflicts ----------------------------------------------------------


def test_stock_that_vanished_gives_409_and_places_nothing(guest, shop):
    _, v = shop
    StockRecord.objects.filter(product=v["M"]).update(num_in_stock=1)

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CONFLICT, response.content
    assert response.json()["code"] == "insufficient_stock"
    assert not Order.objects.exists()
    # The basket is untouched, so the Shopper can fix the line.
    assert guest.get("/api/store/basket").json()["item_count"] == 2


def test_a_sold_out_line_gives_409(guest, shop):
    _, v = shop
    StockRecord.objects.filter(product=v["M"]).update(num_in_stock=0)

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "sold_out"


def test_an_unpublished_product_gives_409(guest, shop):
    product, _ = shop
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.ARCHIVED,
    )

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "unavailable"
    assert not Order.objects.exists()


def test_units_held_by_other_orders_are_not_available(guest, shop):
    _, v = shop
    StockRecord.objects.filter(product=v["M"]).update(num_allocated=4)

    response = guest.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CONFLICT


# --- consent versions and order numbers ---------------------------------------


def test_a_changed_legal_page_gives_409_so_the_shopper_reconfirms(guest, owner):
    shop_settings.update(owner, legal_versions={"distance_sales_contract": "2"})

    response = guest.post(
        CHECKOUT,
        payload(legal_versions={"distance_sales_contract": "1"}),
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "legal_changed"
    assert response.json()["fields"] == {"legal_versions": ["distance_sales_contract"]}
    assert not Order.objects.exists()


def test_matching_legal_versions_are_accepted(guest, owner):
    shop_settings.update(owner, legal_versions={"distance_sales_contract": "2"})

    response = guest.post(
        CHECKOUT,
        payload(legal_versions={"distance_sales_contract": "2"}),
    )

    assert response.status_code == HTTPStatus.CREATED


def test_a_taken_order_number_is_retried(guest, shop, monkeypatch):
    _, v = shop
    from store_backend.services import order_creation  # noqa: PLC0415

    first = guest.post(CHECKOUT, payload()).json()["number"]
    fresh = Guest()
    fill(fresh, v["M"], 1)
    numbers = iter([first, first, "AT-FRESH2"])
    monkeypatch.setattr(order_creation, "_order_number", lambda: next(numbers))

    response = fresh.post(CHECKOUT, payload())

    assert response.status_code == HTTPStatus.CREATED, response.content
    assert response.json()["number"] == "AT-FRESH2"
