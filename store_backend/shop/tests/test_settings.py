# ruff: noqa: PLR2004
from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.services import catalog
from store_backend.services import shop_settings
from store_backend.shop.models import LEGAL_PAGES
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin/settings"
PUBLIC = "/api/store/settings"


def test_staff_cannot_read_or_change_settings(staff_user, api_for):
    api = api_for(staff_user)

    assert api.get(ADMIN).status_code == HTTPStatus.FORBIDDEN
    assert api.patch(ADMIN, {"cod_fee": "10"}).status_code == HTTPStatus.FORBIDDEN
    assert shop_settings.get().cod_fee == 0


def test_owner_reads_defaults(owner, api_for):
    body = api_for(owner, enrol=True).get(ADMIN).json()

    assert body["shipping_fee"] == "0.00"
    assert body["free_shipping_threshold"] is None
    assert body["low_stock_default"] == 2
    assert set(body["legal_versions"]) == set(LEGAL_PAGES)


def test_changing_a_setting_writes_an_audit_entry(owner, api_for):
    api = api_for(owner, enrol=True)

    response = api.patch(
        ADMIN,
        {"shipping_fee": "59.90", "bank_iban": "tr33 0006 1005 1978 6457 8413 26"},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["shipping_fee"] == "59.90"
    assert response.json()["bank_iban"] == "TR330006100519786457841326"
    entry = AuditEntry.objects.get(action="settings_change")
    assert entry.actor == owner
    assert entry.before["shipping_fee"] == "0.00"
    assert entry.after["shipping_fee"] == "59.90"
    assert set(entry.after) == {"shipping_fee", "bank_iban"}


def test_an_unchanged_save_writes_no_audit_entry(owner, api_for):
    api_for(owner, enrol=True).patch(ADMIN, {"cod_fee": "0"})

    assert not AuditEntry.objects.filter(action="settings_change").exists()


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"shipping_fee": "-1"}, "shipping_fee"),
        ({"cod_fee": "abc"}, "cod_fee"),
        ({"fee_kdv_rate": "101"}, "fee_kdv_rate"),
        ({"low_stock_default": -1}, "low_stock_default"),
        ({"bank_iban": "DE123"}, "bank_iban"),
        ({"notification_emails": ["nope"]}, "notification_emails"),
        ({"seller_email": "nope"}, "seller_email"),
        ({"legal_versions": {"made_up": "1"}}, "legal_versions"),
        ({"legal_versions": {"privacy_notice": " "}}, "legal_versions"),
    ],
)
def test_invalid_values_are_rejected(owner, api_for, payload, field):
    response = api_for(owner, enrol=True).patch(ADMIN, payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]


def test_threshold_can_be_cleared_and_versions_merge(owner, api_for):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"free_shipping_threshold": "500"})

    body = api.patch(
        ADMIN,
        {"free_shipping_threshold": None, "legal_versions": {"privacy_notice": "2"}},
    ).json()

    assert body["free_shipping_threshold"] is None
    assert body["legal_versions"]["privacy_notice"] == "2"
    assert body["legal_versions"]["distance_sales_contract"] == "1"


def test_public_read_exposes_only_the_safe_subset(owner, api_for, client):
    api_for(owner, enrol=True).patch(
        ADMIN,
        {
            "shipping_fee": "59.90",
            "bank_iban": "TR330006100519786457841326",
            "notification_emails": ["a@b.co"],
            "seller_name": "Aura Tekstil",
            "banner_image_url": "/media/banner.webp",
        },
    )

    response = client.get(PUBLIC)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["shipping_fee"] == "59.90"
    assert body["seller"]["name"] == "Aura Tekstil"
    assert body["banner"]["image_url"] == "/media/banner.webp"
    assert body["legal_versions"]["privacy_notice"] == "1"
    text = response.content.decode()
    assert "TR33" not in text
    assert "a@b.co" not in text


def test_public_banner_is_null_when_unset(client):
    assert client.get(PUBLIC).json()["banner"] is None


def test_new_products_take_the_low_stock_default(owner):
    shop_settings.update(owner, low_stock_default=5)

    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=None,
        shelf_price="100.00",
    )

    assert product.profile.low_stock_threshold == 5


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"shipping_fee": "99999999999"}, "shipping_fee"),
        ({"shipping_fee": "1e30"}, "shipping_fee"),
        ({"low_stock_default": 99999999999}, "low_stock_default"),
        ({"seller_phone": "5" * 31}, "seller_phone"),
        ({"banner_link": "javascript:alert(1)"}, "banner_link"),
        ({"banner_image_url": "//evil.example/x.png"}, "banner_image_url"),
        ({"bank_name": None}, "bank_name"),
    ],
)
def test_out_of_range_and_unsafe_values_are_422_not_500(owner, api_for, payload, field):
    response = api_for(owner, enrol=True).patch(ADMIN, payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]


def test_safe_urls_are_accepted(owner, api_for):
    response = api_for(owner, enrol=True).patch(
        ADMIN,
        {
            "banner_link": "/kategori/giyim",
            "banner_image_url": "https://cdn.example/b.webp",
        },
    )

    assert response.status_code == HTTPStatus.OK
