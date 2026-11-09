from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from store_backend.api.errors import ApiError
from store_backend.services import staff


class Command(BaseCommand):
    help = (
        "Make EMAIL an Owner (creating the user if needed). The first Owner is made "
        "this way; the Dashboard can only invite Staff."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("email")

    def handle(self, *args, email: str, **options) -> None:
        try:
            user, created = staff.assign_owner(email)
        except ApiError as error:
            raise CommandError(error.message) from error
        verb = "Created" if created else "Updated"
        self.stdout.write(f"{verb} Owner {user.email}.")
        if created:
            self.stdout.write(
                "Set a password with the password reset flow, then enrol TOTP.",
            )
