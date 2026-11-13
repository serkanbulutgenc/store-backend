"""Customer accounts: profile, address book, order history, claiming and deletion."""

from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.models import EmailAddress
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import contact
from store_backend.services import outbox
from store_backend.shop.models import MAX_SAVED_ADDRESSES
from store_backend.shop.models import AccountDeletionRequest
from store_backend.shop.models import SavedAddress
from store_backend.users.models import User

if TYPE_CHECKING:
    from django.db.models import QuerySet

ANONYMISED_DOMAIN = "deleted.invalid"
ADDRESS_NOT_FOUND = "Adres bulunamadı."


# --- profile ------------------------------------------------------------------


def has_pending_deletion(user: User) -> bool:
    return AccountDeletionRequest.objects.filter(
        user=user,
        status=AccountDeletionRequest.Status.PENDING,
    ).exists()


def update_profile(
    user: User,
    *,
    name: str | None = None,
    phone: str | None = None,
    marketing_consent: bool | None = None,
) -> User:
    if name is not None:
        user.name = " ".join(name.split())
    if phone is not None:
        user.phone = contact.clean_phone(phone) if phone.strip() else ""
    if marketing_consent is not None:
        if marketing_consent and user.marketing_consent_at is None:
            user.marketing_consent_at = timezone.now()
        elif not marketing_consent:
            user.marketing_consent_at = None
    user.save(update_fields=["name", "phone", "marketing_consent_at"])
    return user


# --- address book -------------------------------------------------------------


def addresses_of(user: User) -> QuerySet[SavedAddress]:
    return SavedAddress.objects.filter(user=user)


def _clean(data: dict) -> dict:
    name = " ".join(data["full_name"].split())
    if len(name.split()) < contact.MIN_NAME_PARTS:
        msg = "Ad ve soyadınızı girin."
        raise UnprocessableError(msg, fields={"full_name": [msg]})
    address = contact.clean_address(
        "address",
        contact.Address(
            il=data["il"],
            ilce=data["ilce"],
            mahalle=data["mahalle"],
            street=data["street"],
            postal_code=data.get("postal_code", ""),
        ),
    )
    return {
        "full_name": name,
        "phone": contact.clean_phone(data["phone"]),
        "il": address.il,
        "ilce": address.ilce,
        "mahalle": address.mahalle,
        "street": address.street,
        "postal_code": address.postal_code,
    }


def _lock(user: User) -> None:
    """Serialise address changes of one user, so the limit and default hold."""
    User.objects.select_for_update().filter(pk=user.pk).first()


@transaction.atomic
def add_address(user: User, data: dict, *, make_default: bool = False) -> SavedAddress:
    fields = _clean(data)
    _lock(user)
    existing = addresses_of(user)
    if existing.count() >= MAX_SAVED_ADDRESSES:
        msg = f"En fazla {MAX_SAVED_ADDRESSES} adres kaydedebilirsiniz."
        raise ConflictError(msg, code="address_limit")
    default = make_default or not existing.exists()
    if default:
        existing.filter(is_default=True).update(is_default=False)
    return SavedAddress.objects.create(user=user, is_default=default, **fields)


def _owned(user: User, address_id: int) -> SavedAddress:
    address = addresses_of(user).filter(pk=address_id).first()
    if address is None:
        raise NotFoundError(ADDRESS_NOT_FOUND)
    return address


@transaction.atomic
def update_address(
    user: User,
    address_id: int,
    data: dict,
    *,
    make_default: bool | None = None,
) -> SavedAddress:
    _lock(user)
    address = _owned(user, address_id)
    for key, value in _clean(data).items():
        setattr(address, key, value)
    if make_default and not address.is_default:
        addresses_of(user).filter(is_default=True).update(is_default=False)
        address.is_default = True
    address.save()
    return address


@transaction.atomic
def delete_address(user: User, address_id: int) -> None:
    _lock(user)
    address = _owned(user, address_id)
    was_default = address.is_default
    address.delete()
    if was_default:  # the oldest remaining address takes over
        successor = addresses_of(user).order_by("created_at", "pk").first()
        if successor:
            successor.is_default = True
            successor.save(update_fields=["is_default"])


# --- orders -------------------------------------------------------------------


def orders_of(user: User) -> QuerySet[Order]:
    return Order.objects.filter(user=user).order_by("-date_placed", "-pk")


def order_of(user: User, number: str) -> Order:
    order = (
        orders_of(user)
        .filter(number=number.strip().upper())
        .select_related("shipping_address", "billing_address")
        .first()
    )
    if order is None:
        raise NotFoundError
    return order


# --- deletion -----------------------------------------------------------------


def request_deletion(user: User) -> AccountDeletionRequest:
    """Put the account in the Owner queue. One open request at a time."""
    with transaction.atomic():
        _lock(user)
        if has_pending_deletion(user):
            msg = "Hesabınızı silme talebiniz zaten alındı."
            raise ConflictError(msg, code="deletion_pending")
        request = AccountDeletionRequest.objects.create(
            user=user,
            email=user.email,
            name=user.name,
        )
        outbox.notify_owners(
            "deletion_request_owner",
            {
                "name": user.name or "-",
                "email": user.email,
                "admin_url": outbox.admin_url("account-deletions"),
            },
        )
        return request


def pending_deletions() -> QuerySet[AccountDeletionRequest]:
    return AccountDeletionRequest.objects.filter(
        status=AccountDeletionRequest.Status.PENDING,
    ).order_by("requested_at", "pk")


def _pending(request_id: int) -> AccountDeletionRequest:
    request = (
        AccountDeletionRequest.objects.select_for_update()
        .filter(pk=request_id, status=AccountDeletionRequest.Status.PENDING)
        .select_related("user")
        .first()
    )
    if request is None:
        raise NotFoundError
    return request


def anonymise(user: User) -> None:
    """Strip personal fields. Orders stay, with the name and billing address frozen
    on them at placement (and the guest email they were placed with).
    """
    user.email = f"deleted-{user.pk}@{ANONYMISED_DOMAIN}"
    user.name = ""
    user.phone = ""
    user.marketing_consent_at = None
    user.is_active = False
    user.anonymised_at = timezone.now()
    user.set_unusable_password()
    user.save()
    EmailAddress.objects.filter(user=user).delete()
    user.saved_addresses.all().delete()
    user.favorites.all().delete()
    user.store_reviews.all().delete()


@transaction.atomic
def complete_deletion(actor: User, request_id: int) -> AccountDeletionRequest:
    request = _pending(request_id)
    anonymise(request.user)
    request.status = AccountDeletionRequest.Status.COMPLETED
    request.handled_at = timezone.now()
    request.handled_by = actor
    request.save()
    audit.record(
        actor,
        audit.Action.ACCOUNT_DELETION,
        target=request.user,
        summary="Hesap anonimleştirildi",
    )
    return request


@transaction.atomic
def decline_deletion(actor: User, request_id: int) -> AccountDeletionRequest:
    request = _pending(request_id)
    request.status = AccountDeletionRequest.Status.DECLINED
    request.handled_at = timezone.now()
    request.handled_by = actor
    request.save()
    audit.record(
        actor,
        audit.Action.ACCOUNT_DELETION,
        target=request.user,
        summary="Silme talebi reddedildi",
    )
    return request


# --- Dashboard: read-only customer list ---------------------------------------


def customer_queryset(q: str | None = None) -> QuerySet[User]:
    """Shoppers with an account (not Staff), newest first, with their order count.
    Anonymised accounts stay in the list, flagged by `anonymised_at`.
    """
    customers = User.objects.filter(is_staff=False).annotate(
        order_count=Count("orders", distinct=True),
    )
    if q and q.strip():
        term = q.strip()
        customers = customers.filter(Q(name__icontains=term) | Q(email__icontains=term))
    return customers.order_by("-date_joined", "-pk")


def customer_detail(user_id: int) -> User:
    customer = customer_queryset().filter(pk=user_id).first()
    if customer is None:
        raise NotFoundError
    return customer


def orders_for_dashboard(user: User) -> QuerySet[Order]:
    """The customer's orders as list rows, without a query per row."""
    return (
        orders_of(user)
        .select_related("user", "shipping_address")
        .prefetch_related("sources", "lines")
    )
