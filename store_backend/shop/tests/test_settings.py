# ruff: noqa: PLR2004
import importlib
from http import HTTPStatus

import pytest
from django.apps import apps
from oscar.apps.catalogue.models import ProductClass

from store_backend.services import catalog
from store_backend.services import legal_pages
from store_backend.services import shop_settings
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import BankAccount
from store_backend.shop.models import StoreSettings
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
        {
            "shipping_fee": "59.90",
            "bank_accounts": [
                {
                    "name": "Ziraat",
                    "account_holder": "Aura Tekstil",
                    "iban": "tr33 0006 1005 1978 6457 8413 26",
                },
            ],
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["shipping_fee"] == "59.90"
    assert response.json()["bank_accounts"] == [
        {
            "name": "Ziraat",
            "account_holder": "Aura Tekstil",
            "iban": "TR330006100519786457841326",
        },
    ]
    entry = AuditEntry.objects.get(action="settings_change")
    assert entry.actor == owner
    assert entry.before["shipping_fee"] == "0.00"
    assert entry.after["shipping_fee"] == "59.90"
    assert set(entry.after) == {"shipping_fee", "bank_accounts"}
    assert entry.before["bank_accounts"] == []
    assert entry.after["bank_accounts"][0]["iban"] == "TR330006100519786457841326"


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
        (
            {"bank_accounts": [{"name": "Z", "account_holder": "A", "iban": "DE123"}]},
            "bank_accounts",
        ),
        ({"notification_emails": ["nope"]}, "notification_emails"),
        ({"seller_email": "nope"}, "seller_email"),
    ],
)
def test_invalid_values_are_rejected(owner, api_for, payload, field):
    response = api_for(owner, enrol=True).patch(ADMIN, payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]


def test_threshold_can_be_cleared(owner, api_for):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"free_shipping_threshold": "500"})

    body = api.patch(ADMIN, {"free_shipping_threshold": None}).json()

    assert body["free_shipping_threshold"] is None


def test_legal_versions_follow_the_published_pages_and_cannot_be_patched(
    owner,
    api_for,
):
    api = api_for(owner, enrol=True)
    legal_pages.publish(owner, "privacy_notice", title="Gizlilik", body="Yeni metin")

    api.patch(ADMIN, {"legal_versions": {"privacy_notice": "9"}})
    body = api.get(ADMIN).json()

    assert body["legal_versions"]["privacy_notice"] == "2"
    assert body["legal_versions"]["distance_sales_contract"] == "1"


def test_public_read_exposes_only_the_safe_subset(owner, api_for, client):
    api_for(owner, enrol=True).patch(
        ADMIN,
        {
            "shipping_fee": "59.90",
            "bank_accounts": [
                {
                    "name": "Ziraat",
                    "account_holder": "Aura Tekstil",
                    "iban": "TR330006100519786457841326",
                },
            ],
            "notification_emails": ["a@b.co"],
            "seller_name": "Aura Tekstil",
        },
    )

    response = client.get(PUBLIC)

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["shipping_fee"] == "59.90"
    assert body["seller"]["name"] == "Aura Tekstil"
    assert body["legal_versions"]["privacy_notice"] == "1"
    text = response.content.decode()
    assert "TR33" not in text
    assert "a@b.co" not in text


def test_public_read_says_whether_stock_alerts_are_on(owner, api_for, client):
    assert client.get(PUBLIC).json()["stock_alerts_enabled"] is False

    api_for(owner, enrol=True).patch(ADMIN, {"stock_alerts_enabled": True})

    assert client.get(PUBLIC).json()["stock_alerts_enabled"] is True


def test_public_read_has_no_home_banner(client):
    # The home page banner lives in Banners (the `home_hero` slot), not in Settings.
    assert "banner" not in client.get(PUBLIC).json()


def test_new_products_take_the_low_stock_default(owner):
    shop_settings.update(owner, low_stock_default=5)

    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Giyim"),
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
    ],
)
def test_out_of_range_and_unsafe_values_are_422_not_500(owner, api_for, payload, field):
    response = api_for(owner, enrol=True).patch(ADMIN, payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]


# -- bank accounts --------------------------------------------------------------------

IBAN_A = "TR330006100519786457841326"
IBAN_B = "TR320010009999901234567890"


def account(iban=IBAN_A, name="Ziraat", holder="Aura Tekstil"):
    return {"name": name, "account_holder": holder, "iban": iban}


def test_no_bank_account_is_set_at_first(owner, api_for):
    assert api_for(owner, enrol=True).get(ADMIN).json()["bank_accounts"] == []


def test_the_owner_keeps_several_accounts_in_the_order_given(owner, api_for):
    api = api_for(owner, enrol=True)
    body = api.patch(
        ADMIN,
        {"bank_accounts": [account(IBAN_B, "İş Bankası"), account(IBAN_A)]},
    ).json()
    assert [a["iban"] for a in body["bank_accounts"]] == [IBAN_B, IBAN_A]
    assert [a["iban"] for a in api.get(ADMIN).json()["bank_accounts"]] == [
        IBAN_B,
        IBAN_A,
    ]


def test_saving_the_list_replaces_it(owner, api_for):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"bank_accounts": [account(IBAN_A), account(IBAN_B, "Akbank")]})
    body = api.patch(ADMIN, {"bank_accounts": [account(IBAN_B, "Akbank")]}).json()
    assert [a["iban"] for a in body["bank_accounts"]] == [IBAN_B]
    assert api.patch(ADMIN, {"bank_accounts": []}).json()["bank_accounts"] == []


def test_a_save_that_leaves_the_accounts_out_keeps_them(owner, api_for):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"bank_accounts": [account()]})
    body = api.patch(ADMIN, {"cod_fee": "9.00"}).json()
    assert [a["iban"] for a in body["bank_accounts"]] == [IBAN_A]


@pytest.mark.parametrize(
    "accounts",
    [
        [account(iban="DE123")],
        [account(name="  ")],
        [account(holder="")],
        [account(IBAN_A), account(IBAN_A, "Başka")],
        [account(IBAN_A)] * 11,
        [account(name="x" * 101)],
    ],
)
def test_a_bad_account_list_is_refused_whole(owner, api_for, accounts):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"bank_accounts": [account(IBAN_B, "Akbank")]})
    response = api.patch(ADMIN, {"bank_accounts": accounts})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "bank_accounts" in response.json()["fields"]
    # Nothing was half-saved.
    assert [a["iban"] for a in api.get(ADMIN).json()["bank_accounts"]] == [IBAN_B]


def test_an_unchanged_account_list_writes_no_audit_entry(owner, api_for):
    api = api_for(owner, enrol=True)
    api.patch(ADMIN, {"bank_accounts": [account()]})
    saved = AuditEntry.objects.filter(action="settings_change").count()
    api.patch(ADMIN, {"bank_accounts": [account()]})
    assert AuditEntry.objects.filter(action="settings_change").count() == saved


def test_staff_cannot_change_the_accounts(staff_user, api_for):
    response = api_for(staff_user).patch(ADMIN, {"bank_accounts": [account()]})
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_the_old_single_account_becomes_the_first_bank_account():
    migration = importlib.import_module(
        "store_backend.shop.migrations.0023_bank_account",
    )
    StoreSettings.objects.update_or_create(
        pk=1,
        defaults={
            "bank_name": "Ziraat",
            "bank_account_holder": "Aura Tekstil",
            "bank_iban": IBAN_A,
        },
    )
    migration.copy_the_single_account(apps, None)
    assert list(BankAccount.objects.values_list("name", "iban")) == [("Ziraat", IBAN_A)]


def test_nothing_is_copied_when_no_account_was_set():
    migration = importlib.import_module(
        "store_backend.shop.migrations.0023_bank_account",
    )
    migration.copy_the_single_account(apps, None)
    assert not BankAccount.objects.exists()
