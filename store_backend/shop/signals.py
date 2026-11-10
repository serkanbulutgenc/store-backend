from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from store_backend.services import claiming


@receiver(email_confirmed)
def claim_guest_orders(sender, request, email_address, **kwargs) -> None:
    """A verified address claims the guest Orders placed with it, past ones included."""
    claiming.claim_orders(email_address.user, email_address.email)
