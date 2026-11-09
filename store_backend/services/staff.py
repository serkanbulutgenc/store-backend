"""Inviting and deactivating Staff users (never deleting), and the first Owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.forms import default_token_generator
from allauth.account.internal.flows.password_reset import request_password_reset
from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.staff.permissions import OWNER_GROUP
from store_backend.staff.permissions import STAFF_GROUP
from store_backend.users.models import User

if TYPE_CHECKING:
    from django.http import HttpRequest


def normalise_email(email: str) -> str:
    email = email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        msg = "Geçerli bir e-posta adresi girin."
        raise UnprocessableError(msg, fields={"email": [msg]}) from None
    return email


def staff_queryset():
    return User.objects.filter(is_staff=True).order_by("email")


def get_staff_member(pk: int) -> User:
    try:
        return staff_queryset().get(pk=pk)
    except User.DoesNotExist:
        raise NotFoundError from None


@transaction.atomic
def invite(actor: User, request: HttpRequest, *, email: str, name: str = "") -> User:
    """Create a Staff user and email them a link to set a password.

    Possession of that single-use link proves control of the address, so the email is
    marked verified up front.
    """
    email = normalise_email(email)
    if User.objects.filter(email__iexact=email).exists():
        msg = "Bu e-posta adresiyle zaten bir kullanıcı var."
        raise ConflictError(msg, code="email_taken", fields={"email": [msg]})
    user = User(email=email, name=name.strip(), is_staff=True)
    user.set_unusable_password()
    user.save()
    user.groups.add(Group.objects.get(name=STAFF_GROUP))
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    audit.record(
        actor,
        audit.Action.STAFF_INVITED,
        target=user,
        summary=f"{email} davet edildi",
        after={"email": email, "role": "staff"},
    )
    request_password_reset(request, email, [user], default_token_generator)
    return user


@transaction.atomic
def deactivate(actor: User, pk: int) -> User:
    user = get_staff_member(pk)
    if user.pk == actor.pk:
        msg = "Kendi hesabınızı devre dışı bırakamazsınız."
        raise ConflictError(msg, code="cannot_deactivate_self")
    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
        audit.record(
            actor,
            audit.Action.STAFF_DEACTIVATED,
            target=user,
            before={"is_active": True},
            after={"is_active": False},
        )
    return user


@transaction.atomic
def assign_owner(email: str) -> tuple[User, bool]:
    """Make `email` an Owner, creating the user if needed. Returns `(user, created)`.

    This is how the first Owner comes to exist; the Dashboard can only invite Staff.
    """
    email = normalise_email(email)
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"is_staff": True},
    )
    if created:
        user.set_unusable_password()
    user.is_staff = True
    user.save()
    user.groups.add(Group.objects.get(name=OWNER_GROUP))
    EmailAddress.objects.get_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    audit.record(
        None,
        audit.Action.ROLE_CHANGE,
        target=user,
        summary=f"{email} Owner yapıldı",
        after={"role": "owner"},
    )
    return user, created
