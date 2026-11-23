# ruff: noqa: EM101
"""The contact form: categories the Owner manages and the threads Shoppers start.

Open to Guests and signed-in Customers. Abuse limits are a per-IP rate limit and a
hidden honeypot field; neither stores anything. The Shopper gets an automatic
acknowledgement email through the outbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower
from oscar.apps.order.models import Order

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.services import outbox
from store_backend.services import tracking
from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactMessageKind
from store_backend.shop.models import ContactThread

if TYPE_CHECKING:
    from store_backend.users.models import User

NAME_MAX = 100
MESSAGE_MAX = 5000
ORDER_NUMBER_MAX = 50


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


# -- categories (Owner) ------------------------------------------------------------


def categories(*, only_active: bool = False):
    """Categories with their thread counts, in the Owner's order."""
    # Meta.ordering is ignored once a query groups, so order explicitly.
    rows = ContactCategory.objects.annotate(thread_count=Count("threads")).order_by(
        "position",
        "pk",
    )
    return rows.filter(is_active=True) if only_active else rows


def _clean_name(name: str, *, exclude: int | None = None) -> str:
    name = name.strip()
    if not name or len(name) > NAME_MAX:
        raise _invalid("name", f"Ad 1 ile {NAME_MAX} karakter arasında olmalı.")
    same = ContactCategory.objects.annotate(lowered=Lower("name")).filter(
        lowered=name.lower(),
    )
    if exclude is not None:
        same = same.exclude(pk=exclude)
    if same.exists():
        raise _invalid("name", "Bu adla bir kategori var.")
    return name


def create_category(*, name: str) -> ContactCategory:
    last = ContactCategory.objects.order_by("-position").first()
    return ContactCategory.objects.create(
        name=_clean_name(name),
        position=(last.position + 1) if last else 0,
    )


def update_category(
    category_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    position: int | None = None,
) -> ContactCategory:
    category = _category(category_id)
    if name is not None:
        category.name = _clean_name(name, exclude=category.pk)
    if is_active is not None:
        category.is_active = is_active
    if position is not None:
        category.position = position
    category.save()
    return category


def delete_category(category_id: int) -> None:
    category = _category(category_id)
    if category.threads.exists():
        msg = "Bu kategoride yazışmalar var; silmek yerine kapatın."
        raise ConflictError(msg, code="contact_category_in_use")
    category.delete()


def _category(category_id: int) -> ContactCategory:
    category = ContactCategory.objects.filter(pk=category_id).first()
    if category is None:
        raise NotFoundError
    return category


# -- the form (Shoppers) -----------------------------------------------------------


def _linked_order(raw: str) -> Order | None:
    """The Order the typed number names, or None. Never says which it was."""
    if not raw or not tracking.is_order_number(raw):
        return None
    return Order.objects.filter(number=raw).first()


@transaction.atomic
def submit(  # noqa: PLR0913
    *,
    category_id: int,
    name: str,
    email: str,
    message: str,
    order_number: str = "",
    honeypot: str = "",
    ip: str | None,
    user: User | None = None,
) -> ContactThread | None:
    """Store a thread with status New and queue the acknowledgement. Returns None when
    the honeypot was filled: the caller answers as if it worked, and nothing is kept."""
    count_attempt("contact", ip, Rate(*settings.CONTACT_RATE_LIMIT))
    if honeypot.strip():
        return None
    name = name.strip()
    email = email.strip()
    message = message.strip()
    number = tracking.normalise_number(order_number)
    if not name or len(name) > NAME_MAX:
        raise _invalid("name", f"Ad 1 ile {NAME_MAX} karakter arasında olmalı.")
    try:
        validate_email(email)
    except ValidationError:
        raise _invalid("email", "Geçerli bir e-posta adresi girin.") from None
    if not message or len(message) > MESSAGE_MAX:
        raise _invalid(
            "message",
            f"Mesaj 1 ile {MESSAGE_MAX} karakter arasında olmalı.",
        )
    if len(number) > ORDER_NUMBER_MAX:
        raise _invalid("order_number", "Sipariş numarası çok uzun.")
    category = ContactCategory.objects.filter(pk=category_id, is_active=True).first()
    if category is None:
        raise _invalid("category", "Geçerli bir konu seçin.")

    thread = ContactThread.objects.create(
        category=category,
        name=name,
        email=email,
        user=user if user is not None and user.is_authenticated else None,
        order_number=number,
        order=_linked_order(number),
    )
    ContactMessage.objects.create(
        thread=thread,
        kind=ContactMessageKind.SHOPPER,
        body=message,
    )
    outbox.enqueue(
        "contact_acknowledgement",
        to=email,
        context={"name": name, "category": category.name},
    )
    return thread
