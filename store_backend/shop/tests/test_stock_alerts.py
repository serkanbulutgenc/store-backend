from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.messaging.models import Message
from store_backend.services import customers
from store_backend.services import shop_settings
from store_backend.services import stock_alerts
from store_backend.shop.models import AlertConsent
from store_backend.shop.models import StockAlert
from store_backend.shop.models import StoreSettings
from store_backend.shop.tests.conftest import Guest

pytestmark = pytest.mark.django_db

SUBSCRIBE = "/api/store/stock-alerts"
CONFIRM = "/api/store/stock-alerts/confirm"
REJECT = "/api/store/stock-alerts/reject"
EMAIL = "ayse@example.com"
RESENT = 2


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


@pytest.fixture
def on():
    StoreSettings.objects.update_or_create(
        pk=1,
        defaults={"stock_alerts_enabled": True},
    )
    shop_settings.get()


@pytest.fixture
def sold_out(shop):
    return shop[1]["L"]


def body(variant, **extra):
    return {"variant_id": variant.pk, "email": EMAIL, "consent": True, **extra}


def subscribe(variant, guest=None, **extra):
    return (guest or Guest()).post(SUBSCRIBE, body(variant, **extra))


def token_of(email=EMAIL):
    return StockAlert.objects.get(email=email).confirm_token


def test_setting_defaults_to_off_and_stores_nothing(shop, sold_out):
    assert StoreSettings.objects.get_or_create(pk=1)[0].stock_alerts_enabled is False

    response = subscribe(sold_out)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "stock_alerts_unavailable"
    assert not StockAlert.objects.exists()
    assert not Message.objects.exists()


def test_guest_gets_a_confirm_email_and_is_not_yet_waiting(on, sold_out):
    response = subscribe(sold_out)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "confirmation_sent"}
    alert = StockAlert.objects.get()
    assert alert.confirmed_at is None
    assert alert.expires_at - timezone.now() <= timedelta(hours=48)
    message = Message.objects.get()
    assert message.recipient == EMAIL
    assert alert.confirm_token in message.body_text
    assert not AlertConsent.objects.exists()


def test_email_is_lower_cased_and_trimmed(on, sold_out):
    subscribe(sold_out, email="  AYSE@Example.COM ")

    assert StockAlert.objects.get().email == EMAIL


def test_consent_must_be_true(on, sold_out):
    response = subscribe(sold_out, consent=False)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "consent" in response.json()["fields"]
    assert not StockAlert.objects.exists()


def test_in_stock_variant_is_refused(on, shop):
    response = subscribe(shop[1]["M"])

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "in_stock"
    assert not StockAlert.objects.exists()


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.ARCHIVED])
def test_unpublished_product_is_not_found(on, shop, sold_out, status):
    ProductProfile.objects.filter(product=shop[0]).update(status=status)

    assert subscribe(sold_out).status_code == HTTPStatus.NOT_FOUND


def test_confirm_records_consent_with_notice_version_and_time(on, sold_out):
    subscribe(sold_out)

    response = Guest().post(CONFIRM, {"token": token_of()})

    assert response.status_code == HTTPStatus.OK
    alert = StockAlert.objects.get()
    assert alert.confirmed_at is not None
    assert alert.expires_at - alert.confirmed_at == timedelta(days=90)
    consent = AlertConsent.objects.get()
    assert consent.email == EMAIL
    assert consent.variant == sold_out
    assert consent.how_given == AlertConsent.HowGiven.CONFIRM_LINK
    assert consent.notice_version == "1"
    assert consent.rejected_at is None


def test_confirm_twice_changes_nothing(on, sold_out):
    subscribe(sold_out)
    token = token_of()
    Guest().post(CONFIRM, {"token": token})
    first = StockAlert.objects.get()

    response = Guest().post(CONFIRM, {"token": token})

    assert response.status_code == HTTPStatus.OK
    assert StockAlert.objects.get().expires_at == first.expires_at
    assert AlertConsent.objects.count() == 1


def test_expired_confirm_link_is_refused(on, sold_out):
    subscribe(sold_out)
    StockAlert.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = Guest().post(CONFIRM, {"token": token_of()})

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert not AlertConsent.objects.exists()


def test_unknown_token_is_not_found(on):
    assert Guest().post(CONFIRM, {"token": "nope"}).status_code == HTTPStatus.NOT_FOUND


def test_confirm_only_while_sold_out(on, sold_out):
    from store_backend.services import stock  # noqa: PLC0415

    subscribe(sold_out)
    record = stock.record_for(sold_out)
    record.num_in_stock = 3
    record.save()

    response = Guest().post(CONFIRM, {"token": token_of()})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "in_stock"
    assert StockAlert.objects.get().confirmed_at is None


def test_signed_in_shopper_with_verified_email_is_confirmed_at_once(
    on,
    sold_out,
    customer,
    log_in,
):
    me = Guest(log_in(Client(), customer))

    response = me.post(SUBSCRIBE, {"variant_id": sold_out.pk, "consent": True})

    assert response.json() == {"status": "confirmed"}
    alert = StockAlert.objects.get()
    assert alert.email == customer.email
    assert alert.user == customer
    assert alert.confirmed_at is not None
    assert not Message.objects.exists()
    consent = AlertConsent.objects.get()
    assert consent.how_given == AlertConsent.HowGiven.SIGNED_IN
    assert consent.notice_version == "1"


def test_repeat_subscribe_is_one_row_and_one_email(on, sold_out):
    subscribe(sold_out)
    again = subscribe(sold_out)

    assert again.json() == {"status": "confirmation_sent"}
    assert StockAlert.objects.count() == 1
    assert Message.objects.count() == 1


def test_confirm_email_is_resent_after_an_hour(on, sold_out):
    subscribe(sold_out)
    StockAlert.objects.update(
        confirm_sent_at=timezone.now() - timedelta(minutes=61),
    )

    subscribe(sold_out)

    assert Message.objects.count() == RESENT


def test_repeat_subscribe_after_confirming_says_nothing_new(on, sold_out):
    subscribe(sold_out)
    Guest().post(CONFIRM, {"token": token_of()})

    again = subscribe(sold_out)

    assert again.json() == {"status": "confirmation_sent"}
    assert StockAlert.objects.count() == 1
    assert Message.objects.count() == 1


def test_at_most_five_active_per_email(on, shop, sold_out):
    from store_backend.services import catalog  # noqa: PLC0415

    extra = [
        catalog.create_variant(
            shop[0],
            colour=catalog.create_colour(f"Renk {i}", "#ffffff"),
            size="L",
            stock=0,
        )
        for i in range(6)
    ]
    for variant in extra[:5]:
        assert subscribe(variant).status_code == HTTPStatus.OK

    response = subscribe(extra[5])

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "stock_alert_limit"
    assert StockAlert.objects.count() == stock_alerts.MAX_ACTIVE_PER_EMAIL


def test_expired_rows_do_not_count_towards_the_limit(on, shop, sold_out):
    from store_backend.services import catalog  # noqa: PLC0415

    extra = [
        catalog.create_variant(
            shop[0],
            colour=catalog.create_colour(f"Renk {i}", "#ffffff"),
            size="L",
            stock=0,
        )
        for i in range(5)
    ]
    for variant in extra:
        subscribe(variant)
    StockAlert.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    assert subscribe(sold_out).status_code == HTTPStatus.OK


def test_per_ip_rate_limit(on, sold_out, settings):
    settings.STOCK_ALERT_RATE_LIMIT = (2, 3600)

    statuses = [
        subscribe(sold_out, email=f"u{i}@example.com").status_code for i in range(3)
    ]

    assert statuses == [HTTPStatus.OK, HTTPStatus.OK, HTTPStatus.TOO_MANY_REQUESTS]


def test_reject_deletes_the_live_row_and_stamps_the_consent(on, sold_out):
    subscribe(sold_out)
    Guest().post(CONFIRM, {"token": token_of()})
    token = stock_alerts.reject_token(EMAIL, sold_out.pk)

    response = Guest().post(REJECT, {"token": token})

    assert response.status_code == HTTPStatus.OK
    assert not StockAlert.objects.exists()
    assert AlertConsent.objects.get().rejected_at is not None


def test_reject_works_after_the_row_is_gone_and_twice(on, sold_out):
    token = stock_alerts.reject_token(EMAIL, sold_out.pk)

    assert Guest().post(REJECT, {"token": token}).status_code == HTTPStatus.OK
    assert Guest().post(REJECT, {"token": token}).status_code == HTTPStatus.OK


def test_reject_with_a_tampered_token_is_not_found(on):
    assert Guest().post(REJECT, {"token": "x.y.z"}).status_code == HTTPStatus.NOT_FOUND


def test_reject_works_while_the_setting_is_off(sold_out):
    token = stock_alerts.reject_token(EMAIL, sold_out.pk)

    assert Guest().post(REJECT, {"token": token}).status_code == HTTPStatus.OK


def test_anonymising_an_account_deletes_its_live_rows(on, sold_out, customer, log_in):
    me = Guest(log_in(Client(), customer))
    me.post(SUBSCRIBE, {"variant_id": sold_out.pk, "consent": True})
    subscribe(sold_out, email="other@example.com")

    customers.anonymise(customer)

    assert list(StockAlert.objects.values_list("email", flat=True)) == [
        "other@example.com",
    ]
    assert AlertConsent.objects.count() == 1  # the record is kept 3 years
