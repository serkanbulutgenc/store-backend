"""How a stored message leaves the building: the outbox's transport seam.

`Transport.send` makes one attempt and raises on any failure; the outbox records the
error and reschedules. `SmtpTransport` goes through Django's configured email backend.
`InMemoryTransport` records what it was given and can be told to fail, for tests and
for running without a mail relay.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from store_backend.services import shop_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from store_backend.messaging.models import Message


class Transport(Protocol):
    def send(self, message: Message) -> None:
        """Deliver `message` or raise; never swallow an error."""


def _domain() -> str:
    sender = settings.DEFAULT_FROM_EMAIL
    return sender.rsplit("@", 1)[-1].strip("<> ") or "localhost"


class SmtpTransport:
    def send(self, message: Message) -> None:
        store = shop_settings.get()
        email = EmailMultiAlternatives(
            subject=message.subject,
            body=message.body_text,
            to=[message.recipient],
            reply_to=[store.seller_email] if store.seller_email else None,
            headers={"Message-ID": f"<{message.message_id}@{_domain()}>"},
        )
        if message.body_html:
            email.attach_alternative(message.body_html, "text/html")
        email.send(fail_silently=False)


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body_text: str
    body_html: str
    message_id: str


class InMemoryTransport:
    """Keeps what it sends in `sent`. `down` fails every send, `fail_next` some."""

    def __init__(self) -> None:
        self.sent: list[SentMail] = []
        self.down: Exception | None = None
        self._failures: list[Exception] = []

    def fail_next(self, error: Exception, times: int = 1) -> None:
        self._failures.extend([error] * times)

    def send(self, message: Message) -> None:
        if self._failures:
            raise self._failures.pop(0)
        if self.down is not None:
            raise self.down
        self.sent.append(
            SentMail(
                to=message.recipient,
                subject=message.subject,
                body_text=message.body_text,
                body_html=message.body_html,
                message_id=str(message.message_id),
            ),
        )


_current: Transport = SmtpTransport()


def get() -> Transport:
    return _current


@contextmanager
def using(transport: Transport) -> Iterator[Transport]:
    """Swap the transport for the duration of the block."""
    global _current  # noqa: PLW0603
    previous, _current = _current, transport
    try:
        yield transport
    finally:
        _current = previous
