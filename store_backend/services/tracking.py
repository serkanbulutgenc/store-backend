"""Order tracking: the signed link and the number-plus-contact lookup."""

from __future__ import annotations

import phonenumbers
from django.core import signing
from oscar.apps.order.models import Order

from store_backend.api.errors import NotFoundError

LINK_SALT = "order-tracking-link"
LINK_MAX_AGE = 30 * 24 * 60 * 60  # seconds: the link is valid for 30 days

PAYMENT_UNPAID = "unpaid"
PAYMENT_PAID = "paid"
PAYMENT_REFUNDED = "refunded"

NOT_FOUND_MESSAGE = "Sipariş bulunamadı. Bilgileri kontrol edip tekrar deneyin."


def _not_found() -> NotFoundError:
    # One answer for every failure, so a lookup never says which part was wrong.
    return NotFoundError(NOT_FOUND_MESSAGE)


def normalise_number(raw: str) -> str:
    return raw.strip().upper()


def link_token(order: Order) -> str:
    """A signed, timestamped token that opens exactly this Order."""
    return signing.dumps(order.number, salt=LINK_SALT, compress=True)


def order_for_link(token: str) -> Order:
    try:
        number = signing.loads(token, salt=LINK_SALT, max_age=LINK_MAX_AGE)
    except signing.BadSignature:  # includes SignatureExpired
        raise _not_found() from None
    return _by_number(number)


def _by_number(number: str) -> Order:
    order = (
        Order.objects.filter(number=normalise_number(str(number)))
        .select_related("user", "shipping_address")
        .first()
    )
    if order is None:
        raise _not_found()
    return order


def _e164(raw: str) -> str | None:
    try:
        parsed = phonenumbers.parse(raw.strip(), "TR")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _contact_matches(order: Order, contact: str) -> bool:
    contact = contact.strip()
    if "@" in contact:
        wanted = contact.lower()
        emails = {order.guest_email, order.user.email if order.user else ""}
        return wanted in {e.strip().lower() for e in emails if e}
    wanted_phone = _e164(contact)
    address = order.shipping_address
    stored = getattr(getattr(address, "phone_number", None), "as_e164", None)
    return bool(wanted_phone and stored and wanted_phone == stored)


def order_for_lookup(number: str, contact: str) -> Order:
    order = _by_number(number)
    if not _contact_matches(order, contact):
        raise _not_found()
    return order


def payment_status_of(order: Order) -> str:
    """Derived from the payment Sources: nothing debited is unpaid, refunds win."""
    sources = list(order.sources.all())
    if any(s.amount_refunded > 0 for s in sources):
        return PAYMENT_REFUNDED
    if any(s.amount_debited > 0 for s in sources):
        return PAYMENT_PAID
    return PAYMENT_UNPAID


def shipment_of(order: Order) -> tuple[str | None, str | None]:
    """(carrier, tracking number). Staff enter these by hand in the Dashboard orders
    work (ticket 26); until that records them, there is nothing to show."""
    return None, None
