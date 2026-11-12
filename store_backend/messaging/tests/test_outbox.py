# ruff: noqa: PLR2004, F811
import json
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from store_backend.messaging.models import Message
from store_backend.messaging.models import MessageStatus
from store_backend.messaging.transport import SmtpTransport
from store_backend.services import outbox
from store_backend.services import shop_settings
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
WEBHOOK = "/webhooks/email"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture(autouse=True)
def relay(transport):
    return transport


def place(variant, **overrides):
    buyer = Guest(ip="198.51.100.1")
    fill(buyer, variant, quantity=2)
    response = buyer.post(CHECKOUT, payload(**overrides))
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()["number"]


def run_due(*, after=timedelta(0)):
    """What the beat sweep does, at a chosen later time."""
    now = timezone.now() + after
    for pk in Message.objects.filter(status="pending").values_list("pk", flat=True):
        outbox.deliver(pk, now=now)


def test_placing_an_order_stores_the_rendered_email_in_its_transaction(shop, owner):
    shop_settings.update(
        owner,
        seller_name="Aura Tekstil Ltd",
        bank_iban="TR330006100519786457841326",
        bank_account_holder="Aura Tekstil",
    )
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    assert message.status == MessageStatus.PENDING
    assert message.recipient == payload()["email"]
    assert message.recipient == message.order.guest_email
    assert number in message.subject
    assert "TR330006100519786457841326" in message.body_text
    assert "Aura Tekstil Ltd" in message.body_html
    assert "/siparis-takip?t=" in message.body_text


def test_cod_email_has_no_bank_details(shop, owner):
    shop_settings.update(owner, bank_iban="TR330006100519786457841326")
    number = place(shop[1]["M"], payment_method="cod", cod_fee_consent=True)
    message = Message.objects.get(order__number=number)
    assert "TR330006100519786457841326" not in message.body_text
    assert "Kapıda ödeme bedeli" in message.body_text


def test_order_placement_survives_a_dead_smtp_server_and_delivers_later(shop, relay):
    relay.down = ConnectionRefusedError("Connection refused")
    number = place(shop[1]["M"])  # 201, asserted inside
    message = Message.objects.get(order__number=number)

    run_due()
    message.refresh_from_db()
    assert message.status == MessageStatus.PENDING
    assert message.attempts == 1
    assert "Connection refused" in message.last_error
    assert message.next_attempt_at > timezone.now()
    assert relay.sent == []

    relay.down = None
    run_due()  # not due yet: still waiting out the back-off
    assert relay.sent == []

    run_due(after=timedelta(minutes=2))
    message.refresh_from_db()
    assert message.status == MessageStatus.SENT
    assert message.sent_at is not None
    assert message.last_error == ""
    assert len(relay.sent) == 1
    sent = relay.sent[0]
    assert sent.to == message.recipient
    assert sent.body_html
    assert sent.message_id == str(message.message_id)


def test_a_permanently_failing_message_stays_visible_with_its_error(
    shop,
    staff,
    relay,
):
    relay.down = ConnectionRefusedError("Connection refused")
    number = place(shop[1]["M"])
    day = timedelta(days=0)
    for _ in range(outbox.MAX_ATTEMPTS):
        day += timedelta(days=1)
        run_due(after=day)
    message = Message.objects.get(order__number=number)
    assert message.status == MessageStatus.FAILED
    assert message.attempts == outbox.MAX_ATTEMPTS
    assert "Connection refused" in message.last_error

    detail = staff.get(f"{ORDERS}/{number}").json()
    assert [m["status"] for m in detail["messages"]] == ["failed"]
    assert "Connection refused" in detail["messages"][0]["error"]
    assert detail["messages"][0]["body_text"]
    assert detail["messages"][0]["recipient"] == message.recipient

    relay.down = None
    run_due(after=timedelta(days=30))  # failed is final: never retried by the sweep
    assert relay.sent == []


def test_retries_span_about_a_day():
    total = sum(outbox.RETRY_DELAYS, timedelta())
    assert len(outbox.RETRY_DELAYS) == 5
    assert timedelta(hours=20) < total <= timedelta(hours=24)


def test_resend_queues_a_copy_and_keeps_the_original(shop, staff, relay):
    relay.down = ConnectionRefusedError("Connection refused")
    number = place(shop[1]["M"])
    for day in range(1, outbox.MAX_ATTEMPTS + 1):
        run_due(after=timedelta(days=day))
    original = Message.objects.get(order__number=number)
    relay.down = None

    response = staff.post(f"{ORDERS}/{number}/messages/{original.pk}/resend")
    assert response.status_code == HTTPStatus.OK, response.content
    statuses = [m["status"] for m in response.json()["messages"]]
    assert statuses == ["failed", "pending"]

    run_due()
    assert len(relay.sent) == 1
    copy = Message.objects.get(resent_from=original)
    assert copy.status == MessageStatus.SENT
    assert AuditEntry.objects.filter(action="message_resend").count() == 1


def test_resend_of_another_orders_message_is_not_found(shop, staff):
    a = place(shop[1]["M"])
    b = place(shop[1]["M"])
    foreign = Message.objects.get(order__number=a)
    response = staff.post(f"{ORDERS}/{b}/messages/{foreign.pk}/resend")
    assert response.status_code == HTTPStatus.NOT_FOUND


def post_event(client, body, *, token="s3cret"):  # noqa: S107
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        WEBHOOK,
        json.dumps(body),
        content_type="application/json",
        headers=headers,
    )


def test_bounce_webhook_marks_the_message_bounced(shop, staff, settings):
    settings.EMAIL_WEBHOOK_SECRET = "s3cret"  # noqa: S105
    number = place(shop[1]["M"])
    run_due()
    message = Message.objects.get(order__number=number)
    assert message.status == MessageStatus.SENT

    response = post_event(
        Client(),
        {
            "message_id": str(message.message_id),
            "type": "bounce",
            "reason": "550 no such user",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"marked": 1}
    message.refresh_from_db()
    assert message.status == MessageStatus.BOUNCED
    assert message.bounced_at is not None

    row = staff.get(f"{ORDERS}/{number}").json()["messages"][0]
    assert row["status"] == "bounced"
    assert row["error"] == "550 no such user"


def test_complaint_also_marks_bounced_and_a_list_is_accepted(shop, settings):
    settings.EMAIL_WEBHOOK_SECRET = "s3cret"  # noqa: S105
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    response = post_event(
        Client(),
        [
            {"message_id": str(message.message_id), "type": "complaint"},
            {"message_id": "not-a-uuid", "type": "bounce"},
            {"message_id": str(message.message_id), "type": "delivered"},
        ],
    )
    assert response.json() == {"marked": 1}
    message.refresh_from_db()
    assert message.status == MessageStatus.BOUNCED
    assert message.bounce_reason == "complaint"


@pytest.mark.parametrize("token", [None, "wrong"])
def test_webhook_needs_the_shared_secret(shop, settings, token):
    settings.EMAIL_WEBHOOK_SECRET = "s3cret"  # noqa: S105
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    response = post_event(
        Client(),
        {"message_id": str(message.message_id), "type": "bounce"},
        token=token,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    message.refresh_from_db()
    assert message.status == MessageStatus.PENDING


def test_webhook_is_closed_when_no_secret_is_configured(settings):
    settings.EMAIL_WEBHOOK_SECRET = ""
    response = post_event(Client(), {"type": "bounce"}, token="")
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_unauthenticated_cannot_resend(shop):
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    response = Client().post(f"{ORDERS}/{number}/messages/{message.pk}/resend")
    assert response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


def test_the_beat_sweep_delivers_due_messages_through_the_task(shop, relay):
    place(shop[1]["M"])
    assert outbox.dispatch_due() == 1  # eager Celery in tests
    assert len(relay.sent) == 1
    assert outbox.dispatch_due() == 0


def test_a_broken_template_never_costs_the_order(shop, monkeypatch):
    monkeypatch.setitem(outbox.SUBJECTS, "order_placed", "{no_such_field}")
    number = place(shop[1]["M"])  # still 201
    assert not Message.objects.filter(order__number=number).exists()


def test_an_unexpected_send_error_is_recorded_not_retried_forever(shop, relay):
    number = place(shop[1]["M"])
    relay.fail_next(ValueError("bad header"))
    run_due()
    message = Message.objects.get(order__number=number)
    assert message.attempts == 1
    assert "bad header" in message.last_error


def test_the_inline_scheduler_delivers_without_a_worker(shop, relay):
    from store_backend.messaging import scheduler  # noqa: PLC0415

    place(shop[1]["M"])
    with scheduler.using(scheduler.InlineScheduler()):
        assert outbox.dispatch_due() == 1
    assert len(relay.sent) == 1


def test_a_scheduler_that_cannot_queue_leaves_the_message_for_the_sweep(shop, relay):
    from store_backend.messaging import scheduler  # noqa: PLC0415

    class Broken:
        def kick(self, message_id):
            msg = "broker down"
            raise ConnectionError(msg)

    with scheduler.using(Broken()):
        number = place(shop[1]["M"])  # still 201
    assert Message.objects.get(order__number=number).status == MessageStatus.PENDING
    assert outbox.dispatch_due() == 1
    assert len(relay.sent) == 1


def test_the_smtp_transport_sends_through_the_email_backend(shop):
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    SmtpTransport().send(message)

    sent = mail.outbox[0]
    assert sent.to == [message.recipient]
    assert sent.alternatives[0][1] == "text/html"
    assert str(message.message_id) in sent.extra_headers["Message-ID"]


def test_a_pending_message_cannot_be_resent(shop, staff):
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    response = staff.post(f"{ORDERS}/{number}/messages/{message.pk}/resend")
    assert response.status_code == HTTPStatus.CONFLICT


def test_a_repeated_bounce_event_changes_nothing(shop, settings):
    settings.EMAIL_WEBHOOK_SECRET = "s3cret"  # noqa: S105
    number = place(shop[1]["M"])
    message = Message.objects.get(order__number=number)
    event = {"message_id": str(message.message_id), "type": "bounce", "reason": "a"}
    post_event(Client(), event)
    message.refresh_from_db()
    first = message.bounced_at
    post_event(Client(), {**event, "reason": "b"})
    message.refresh_from_db()
    assert message.bounced_at == first
    assert message.bounce_reason == "a"
