# ruff: noqa: PLR2004, F811
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import mail
from django.utils import timezone
from oscar.apps.order.models import Order
from oscar.apps.partner.models import StockRecord

from store_backend.messaging.models import Message
from store_backend.services import order_ops
from store_backend.services import outbox
from store_backend.services import shop_settings
from store_backend.services import stock
from store_backend.shop.tasks import process_unpaid_transfers
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import shop  # noqa: F401
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

ORDERS = "/api/admin/orders"
IBAN = "TR330006100519786457841326"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


def place(variant, **overrides):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=2)
    response = buyer.post(CHECKOUT, payload(**overrides))
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()["number"]


def templates(number=None):
    rows = Message.objects.all()
    if number:
        rows = rows.filter(order__number=number)
    return [m.template for m in rows]


def act(api, number, action, data=None):
    response = api.post(f"{ORDERS}/{number}/{action}", data or {})
    assert response.status_code == HTTPStatus.OK, response.content
    return response


def total_of(number):
    return str(Order.objects.get(number=number).total_incl_tax)


# --- the order email ------------------------------------------------------------


def test_order_email_shows_bank_details_number_and_seller_footer(shop, owner):
    shop_settings.update(
        owner,
        seller_name="Aura Tekstil Ltd",
        seller_address="Mersin Merkez",
        seller_email="info@aura.example",
        bank_iban=IBAN,
        bank_account_holder="Aura Tekstil",
    )
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    for body in (message.body_text, message.body_html):
        assert IBAN in body
        assert "Aura Tekstil" in body
        assert number in body
        assert "Mersin Merkez" in body
        assert "info@aura.example" in body
    # nothing about where the Shopper lives or how to call them
    order = Order.objects.get(number=number)
    assert order.shipping_address.line1 not in message.body_text
    assert str(order.shipping_address.phone_number) not in message.body_text


def test_order_link_is_a_signed_token_that_expires_in_thirty_days(shop):
    from store_backend.services import tracking  # noqa: PLC0415

    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    token = message.body_text.split("siparis-takip?t=")[1].split()[0]
    assert tracking.order_for_link(token).number == number
    assert tracking.LINK_MAX_AGE == 30 * 24 * 60 * 60


# --- staff notifications --------------------------------------------------------


def test_staff_get_one_email_per_new_order(shop, owner):
    shop_settings.update(
        owner,
        notification_emails=["a@aura.example", "b@aura.example"],
    )
    number = place(shop[1]["M"])
    staff_mail = Message.objects.filter(template="staff_new_order")
    assert sorted(m.recipient for m in staff_mail) == [
        "a@aura.example",
        "b@aura.example",
    ]
    assert all(m.order is None for m in staff_mail)
    assert number in staff_mail[0].subject
    assert f"/admin/orders/{number}" in staff_mail[0].body_text


def test_low_stock_digest_goes_to_the_recipients_only_when_something_is_low(
    shop,
    owner,
):
    shop_settings.update(owner, notification_emails=["a@aura.example"])
    variant = shop[1]["M"]
    StockRecord.objects.filter(product=variant).update(num_in_stock=50)
    Message.objects.all().delete()

    StockRecord.objects.filter(product=variant).update(num_in_stock=1)
    count = stock.send_low_stock_digest()
    assert count >= 1
    digest = Message.objects.get(template="low_stock_digest")
    assert digest.recipient == "a@aura.example"
    assert variant.variant_profile.sku in digest.body_text


def test_the_owner_is_emailed_about_a_deletion_request(owner, customer, log_in):
    from django.test import Client  # noqa: PLC0415

    client = Client()
    token = log_in(client, customer)
    response = client.post(
        "/api/store/account/deletion-request",
        content_type="application/json",
        headers={"X-Session-Token": token},
    )
    assert response.status_code == HTTPStatus.CREATED, response.content
    message = Message.objects.get(template="deletion_request_owner")
    assert message.recipient == owner.email
    assert customer.email in message.body_text


# --- shopper emails along the order lifecycle ------------------------------------


def test_the_lifecycle_sends_the_right_emails_and_no_delivered_mail(shop, staff, boss):
    number = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    act(staff, number, "ship", {"carrier": "Yurtiçi", "tracking_number": "YK123"})
    act(staff, number, "deliver")
    act(staff, number, "request-return")
    act(staff, number, "mark-returned")
    act(boss, number, "refund", {"amount": total_of(number), "method": "cash"})
    assert templates(number) == [
        "order_placed",
        "order_shipped",
        "return_received",
        "refund_issued",
    ]
    shipped = Message.objects.get(template="order_shipped")
    assert "YK123" in shipped.body_text
    assert "Yurtiçi" in shipped.body_html


def test_paid_and_cancelled_emails(shop, staff):
    number = place(shop[1]["M"])
    act(staff, number, "pay", {"amount": total_of(number)})
    assert templates(number) == ["order_placed", "order_paid"]
    other = place(shop[1]["M"])
    act(staff, other, "cancel", {"reason": "Stok yok"})
    cancelled = Message.objects.get(order__number=other, template="order_cancelled")
    assert "Stok yok" in cancelled.body_text


def test_correcting_the_email_resends_the_order_email_to_the_new_address(shop, staff):
    number = place(shop[1]["M"])
    response = staff.patch(f"{ORDERS}/{number}/email", {"email": "yeni@example.com"})
    assert response.status_code == HTTPStatus.OK
    rows = list(Message.objects.filter(order__number=number))
    assert [m.template for m in rows] == ["order_placed", "order_placed"]
    assert rows[1].recipient == "yeni@example.com"


def test_a_broken_template_never_costs_the_order_action(shop, staff, monkeypatch):
    number = place(shop[1]["M"])
    monkeypatch.setattr(outbox, "render", lambda *a, **k: 1 / 0)
    act(staff, number, "pay", {"amount": total_of(number)})
    assert templates(number) == ["order_placed"]


# --- account emails go through the outbox ----------------------------------------


def test_verification_mail_is_logged_in_turkish_and_survives_a_dead_relay(
    settings,
    client,
):
    settings.EMAIL_BACKEND = "store_backend.messaging.tests.backends.FlakyBackend"
    from store_backend.messaging.tests.backends import FlakyBackend  # noqa: PLC0415

    FlakyBackend.down = True
    try:
        response = client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "yeni@example.com", "password": "correct-horse-battery-staple"},
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.UNAUTHORIZED  # verification pending
        message = Message.objects.get(recipient="yeni@example.com")
        assert message.template.startswith("email_confirmation")
        assert "doğrulamanız" in message.body_text
        assert "/hesap/eposta-dogrula/" in message.body_text
        outbox.deliver(message.pk, now=timezone.now() + timedelta(minutes=1))
        message.refresh_from_db()
        assert message.attempts == 1
        assert message.status == "pending"
    finally:
        FlakyBackend.down = False
    outbox.deliver(message.pk, now=timezone.now() + timedelta(minutes=5))
    assert [m.to for m in mail.outbox] == [["yeni@example.com"]]


# --- timed jobs -------------------------------------------------------------------


def test_reminder_at_24h_then_auto_cancel_at_48h_with_stock_released(shop):
    variant = shop[1]["M"]
    number = place(variant)
    placed = Order.objects.get(number=number).date_placed
    assert StockRecord.objects.get(product=variant).num_allocated == 2

    assert order_ops.send_payment_reminders(now=placed + timedelta(hours=23)) == 0
    assert order_ops.send_payment_reminders(now=placed + timedelta(hours=25)) == 1
    assert order_ops.send_payment_reminders(now=placed + timedelta(hours=26)) == 0
    assert order_ops.cancel_unpaid_transfers(now=placed + timedelta(hours=47)) == 0

    assert order_ops.cancel_unpaid_transfers(now=placed + timedelta(hours=49)) == 1
    order = Order.objects.get(number=number)
    assert order.status == "cancelled"
    assert StockRecord.objects.get(product=variant).num_allocated == 0
    assert templates(number) == [
        "order_placed",
        "payment_reminder",
        "order_auto_cancelled",
    ]
    entry = AuditEntry.objects.get(action="cancel", target_id=str(order.pk))
    assert entry.actor is None
    assert order_ops.cancel_unpaid_transfers(now=placed + timedelta(hours=50)) == 0


def test_paid_cod_and_already_cancelled_orders_are_left_alone(shop, staff):
    paid = place(shop[1]["M"])
    act(staff, paid, "pay", {"amount": total_of(paid)})
    cod = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    later = timezone.now() + timedelta(days=3)
    assert order_ops.send_payment_reminders(now=later) == 0
    assert order_ops.cancel_unpaid_transfers(now=later) == 0
    assert Order.objects.get(number=paid).status == "preparing"
    assert Order.objects.get(number=cod).status == "preparing"


def test_the_beat_task_runs_both_jobs(shop):
    place(shop[1]["M"])
    assert process_unpaid_transfers() == {"reminded": 0, "cancelled": 0}


def test_an_overdue_order_gets_no_late_reminder(shop):
    number = place(shop[1]["M"])
    placed = Order.objects.get(number=number).date_placed
    assert order_ops.send_payment_reminders(now=placed + timedelta(hours=60)) == 0
    assert order_ops.cancel_unpaid_transfers(now=placed + timedelta(hours=60)) == 1
    assert templates(number) == ["order_placed", "order_auto_cancelled"]


def test_one_failing_order_does_not_stop_the_batch(shop, monkeypatch):
    first = place(shop[1]["M"])
    second = place(shop[1]["M"])
    placed = Order.objects.get(number=second).date_placed
    real = order_ops._release_stock  # noqa: SLF001

    def flaky(order):
        if order.number == first:
            msg = "boom"
            raise RuntimeError(msg)
        real(order)

    monkeypatch.setattr(order_ops, "_release_stock", flaky)
    assert order_ops.cancel_unpaid_transfers(now=placed + timedelta(hours=49)) == 1
    assert Order.objects.get(number=first).status == "new"
    assert Order.objects.get(number=second).status == "cancelled"


def test_a_resent_order_email_has_no_payment_instructions_once_paid(
    shop,
    staff,
    owner,
):
    shop_settings.update(owner, bank_iban=IBAN)
    number = place(shop[1]["M"])
    act(staff, number, "pay", {"amount": total_of(number)})
    staff.patch(f"{ORDERS}/{number}/email", {"email": "yeni@example.com"})
    resent = Message.objects.filter(
        order__number=number,
        template="order_placed",
    ).last()
    assert resent.recipient == "yeni@example.com"
    assert IBAN not in resent.body_text
    assert IBAN not in resent.body_html
