from django.core.management.base import BaseCommand

from store_backend.messaging.models import Message
from store_backend.messaging.models import MessageStatus


class Command(BaseCommand):
    help = (
        "Print how many outbox messages are `failed` (gave up after all retries). "
        "store-infra's alert script compares it with the last value it saw."
    )

    def handle(self, *args, **options) -> None:
        self.stdout.write(
            str(Message.objects.filter(status=MessageStatus.FAILED).count()),
        )
