from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from store_backend.messaging.models import Message
from store_backend.messaging.models import MessageStatus

pytestmark = pytest.mark.django_db


def make(status):
    return Message.objects.create(
        recipient="a@example.com",
        template="order_placed",
        subject="s",
        body_text="t",
        body_html="h",
        status=status,
        next_attempt_at=timezone.now() + timedelta(hours=1),
    )


def test_counts_only_failed_messages():
    make(MessageStatus.FAILED)
    make(MessageStatus.FAILED)
    make(MessageStatus.PENDING)
    make(MessageStatus.BOUNCED)
    out = StringIO()
    call_command("outbox_failed_count", stdout=out)
    assert out.getvalue().strip() == "2"
