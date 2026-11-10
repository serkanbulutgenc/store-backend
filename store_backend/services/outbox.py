"""The message outbox: write in the caller's transaction, send from a worker.

`enqueue` renders a Turkish HTML + text email from a repo template and Store settings
and stores it as a `Message` in the caller's transaction, so an Order never exists
without its email or the other way round. Sending happens later (`deliver`, run by
Celery): a failure reschedules the message with back-off, and after the last attempt
it stays visible as `failed` with its error. SMTP trouble never reaches the caller.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.messaging.models import Message
from store_backend.messaging.models import MessageStatus
from store_backend.services import pre_information
from store_backend.services import shop_settings

if TYPE_CHECKING:
    from datetime import datetime

    from oscar.apps.order.models import Order

    from store_backend.shop.models import StoreSettings

logger = logging.getLogger(__name__)

# Wait before attempt 2, 3, ...: five retries over about 23 hours after the first try.
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=16),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
ERROR_MAX = 500
PAYMENT_WINDOW = timedelta(hours=48)

# Template name -> subject. Each has `messaging/<name>.html` and `.txt`.
SUBJECTS: dict[str, str] = {
    "order_placed": "Siparişiniz alındı – {number}",
}


def _subject(template: str, context: dict[str, Any]) -> str:
    return SUBJECTS[template].format(**context)


def render(template: str, context: dict[str, Any]) -> tuple[str, str, str]:
    """(subject, text, html) for a template, with shop details from Store settings."""
    store = shop_settings.get()
    full = {"store": store, "shop_name": shop_name(store), **context}
    return (
        _subject(template, full),
        render_to_string(f"messaging/{template}.txt", full).strip(),
        render_to_string(f"messaging/{template}.html", full),
    )


def shop_name(store: StoreSettings) -> str:
    return store.seller_name or "Aura Tekstil"


def order_context(order: Order, store: StoreSettings) -> dict[str, Any]:
    """The facts every order email draws on. Links never carry phone or address."""
    lines = [
        {
            "title": line.product.parent.title,
            "colour": line.product.variant_profile.colour.name,
            "size": line.product.variant_profile.size,
            "quantity": line.quantity,
            "total": pre_information.format_try(line.line_price_incl_tax),
        }
        for line in sorted(order.lines.all(), key=lambda row: row.pk)
    ]
    from store_backend.services import tracking  # noqa: PLC0415  (it imports checkout)

    base = settings.STOREFRONT_URL.rstrip("/")
    return {
        "number": order.number,
        "lines": lines,
        "total": pre_information.format_try(order.total_incl_tax),
        "shipping_fee": pre_information.format_try(order.shipping_incl_tax),
        "tracking_url": f"{base}/siparis-takip?t={tracking.link_token(order)}",
        "pay_by": timezone.localtime(order.date_placed + PAYMENT_WINDOW),
    }


def enqueue(
    template: str,
    *,
    to: str,
    context: dict[str, Any],
    order: Order | None = None,
) -> Message:
    """Store a rendered message; it is sent once the caller's transaction commits."""
    subject, text, html = render(template, context)
    message = Message.objects.create(
        order=order,
        recipient=to,
        template=template,
        subject=subject,
        body_text=text,
        body_html=html,
        next_attempt_at=timezone.now(),
    )
    _schedule(message.pk)
    return message


def _schedule(message_id: int) -> None:
    from store_backend.messaging import tasks  # noqa: PLC0415

    def send() -> None:
        try:
            tasks.deliver_message.delay(message_id)
        except Exception:
            # A broker outage must not undo the Order; the beat sweep picks it up.
            logger.warning("Could not queue message %s", message_id, exc_info=True)

    transaction.on_commit(send)


def _email_for(message: Message) -> EmailMultiAlternatives:
    store = shop_settings.get()
    email = EmailMultiAlternatives(
        subject=message.subject,
        body=message.body_text,
        to=[message.recipient],
        reply_to=[store.seller_email] if store.seller_email else None,
        headers={"Message-ID": f"<{message.message_id}@{_domain()}>"},
    )
    email.attach_alternative(message.body_html, "text/html")
    return email


def _domain() -> str:
    sender = settings.DEFAULT_FROM_EMAIL
    return sender.rsplit("@", 1)[-1].strip("<> ") or "localhost"


def deliver(message_id: int, *, now: datetime | None = None) -> Message | None:
    """One attempt at one due message. Returns it, or None if it was not due."""
    now = now or timezone.now()
    with transaction.atomic():
        message = (
            Message.objects.select_for_update(skip_locked=True)
            .filter(
                pk=message_id,
                status=MessageStatus.PENDING,
                next_attempt_at__lte=now,
            )
            .first()
        )
        if message is None:
            return None
        message.attempts += 1
        try:
            _email_for(message).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001  any send error is recorded, never lost
            _failed_attempt(message, exc, now)
        else:
            message.status = MessageStatus.SENT
            message.sent_at = now
            message.last_error = ""
        message.save()
    return message


def _failed_attempt(message: Message, exc: Exception, now: datetime) -> None:
    message.last_error = f"{type(exc).__name__}: {exc}"[:ERROR_MAX]
    if message.attempts >= MAX_ATTEMPTS:
        message.status = MessageStatus.FAILED
        logger.error("Message %s failed for good: %s", message.pk, message.last_error)
    else:
        message.next_attempt_at = now + RETRY_DELAYS[message.attempts - 1]


def dispatch_due(*, now: datetime | None = None) -> int:
    """Queue every pending message whose time has come. Returns how many."""
    from store_backend.messaging import tasks  # noqa: PLC0415

    now = now or timezone.now()
    ids = list(
        Message.objects.filter(
            status=MessageStatus.PENDING,
            next_attempt_at__lte=now,
        ).values_list("pk", flat=True),
    )
    for message_id in ids:
        tasks.deliver_message.delay(message_id)
    return len(ids)


@transaction.atomic
def resend(message_id: int, *, order: Order) -> Message:
    """Queue a fresh copy of a logged message; the original stays as history."""
    original = Message.objects.filter(pk=message_id, order=order).first()
    if original is None:
        raise NotFoundError
    if original.status == MessageStatus.PENDING:
        msg = "Bu e-posta zaten gönderilmeyi bekliyor."
        raise ConflictError(msg, code="message_pending")
    copy = Message.objects.create(
        order=order,
        recipient=original.recipient,
        template=original.template,
        subject=original.subject,
        body_text=original.body_text,
        body_html=original.body_html,
        next_attempt_at=timezone.now(),
        resent_from=original,
    )
    _schedule(copy.pk)
    return copy


@transaction.atomic
def mark_bounced(message_id: str, reason: str) -> bool:
    """Record a bounce or complaint reported by the relay. False if unknown."""
    try:
        message = (
            Message.objects.select_for_update().filter(message_id=message_id).first()
        )
    except ValidationError:  # not a UUID
        return False
    if message is None:
        return False
    if message.status == MessageStatus.BOUNCED:
        return True  # a repeated event changes nothing
    message.status = MessageStatus.BOUNCED
    message.bounced_at = timezone.now()
    message.bounce_reason = reason[:ERROR_MAX]
    message.save(update_fields=["status", "bounced_at", "bounce_reason"])
    return True
