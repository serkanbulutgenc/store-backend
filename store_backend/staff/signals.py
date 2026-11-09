from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from store_backend.services import audit


@receiver(user_logged_in)
def audit_staff_login(sender, request, user, **kwargs) -> None:
    if user.is_staff:
        audit.record(user, audit.Action.LOGIN, target=user)
