import sentry_sdk
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError


class Command(BaseCommand):
    help = (
        "Send one forced exception to the error tracker, with an email and a phone in "
        "its text, to prove it arrives scrubbed. Needs SENTRY_DSN."
    )

    def handle(self, *args, **options) -> None:
        if not sentry_sdk.get_client().is_active():
            msg = "SENTRY_DSN is not set; nothing would be sent."
            raise CommandError(msg)
        try:
            msg = "forced test error for test.shopper@example.com, 0532 123 45 67"
            raise RuntimeError(msg)  # noqa: TRY301
        except RuntimeError:
            sentry_sdk.capture_exception()
        sentry_sdk.flush(timeout=10)
        self.stdout.write(
            "Sent. Check the tracker: no email or phone should be visible.",
        )
