"""Stock alerts: a Shopper asks to be emailed once when a sold-out Variant is back.

A commercial message (ETK), so the Store setting keeps it off until the Owner has
registered with İYS. A guest confirms by email (the click is the consent); a signed-in
Shopper with a verified email is confirmed at once. `StockAlert` is the live row,
`AlertConsent` the 3-year proof that outlives it.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone
from oscar.apps.catalogue.models import Product

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.catalog.models import ProductStatus
from store_backend.services import legal_pages
from store_backend.services import outbox
from store_backend.services import shop_settings
from store_backend.services import stock
from store_backend.shop.models import AlertConsent
from store_backend.shop.models import StockAlert

if TYPE_CHECKING:
    from store_backend.users.models import User

UNCONFIRMED_TTL = timedelta(hours=48)
ALERT_TTL = timedelta(days=90)
CONSENT_TTL = timedelta(days=365 * 3)
RESEND_AFTER = timedelta(hours=1)
MAX_ACTIVE_PER_EMAIL = 5
REJECT_SALT = "stock-alert-reject"

CONFIRMED = "confirmed"
CONFIRMATION_SENT = "confirmation_sent"


def reject_token(email: str, variant_id: int) -> str:
    """A signed value for the reject link: no row needed, never expires, so the link
    in an old email still works after the live row is gone."""
    return signing.dumps(
        {"e": email.strip().lower(), "v": variant_id},
        salt=REJECT_SALT,
    )


def _unavailable() -> ConflictError:
    msg = "Stok bildirimi şu anda kullanılamıyor."
    return ConflictError(msg, code="stock_alerts_unavailable")


def _variant(variant_id: int) -> Product:
    """A Variant of a published Product, or 404."""
    variant = (
        Product.objects.filter(
            pk=variant_id,
            parent__isnull=False,
            parent__profile__status=ProductStatus.PUBLISHED,
        )
        .select_related("variant_profile__colour", "parent")
        .first()
    )
    if variant is None:
        raise NotFoundError
    return variant


def _sold_out(variant: Product) -> bool:
    return stock.units(stock.record_for(variant)) == 0


def _in_stock() -> ConflictError:
    msg = "Bu ürün stokta."
    return ConflictError(msg, code="in_stock")


def _notice_version() -> str:
    try:
        return str(legal_pages.current("privacy_notice").version)
    except NotFoundError:
        raise _unavailable() from None


def _verified(user: User | None) -> bool:
    return bool(
        user is not None
        and user.is_authenticated
        and EmailAddress.objects.filter(
            user=user,
            email__iexact=user.email,
            verified=True,
        ).exists(),
    )


def _active(email: str):
    return StockAlert.objects.filter(
        email__iexact=email,
        expires_at__gt=timezone.now(),
    )


def _send_confirm(alert: StockAlert, variant: Product) -> None:
    base = settings.STOREFRONT_URL.rstrip("/")
    profile = variant.variant_profile
    outbox.enqueue(
        "stock_alert_confirm",
        to=alert.email,
        context={
            "product": variant.parent.title,
            "colour": profile.colour.name,
            "size": profile.size,
            "confirm_url": f"{base}/stok-bildirimi/onayla?token={alert.confirm_token}",
        },
    )
    alert.confirm_sent_at = timezone.now()
    alert.save(update_fields=["confirm_sent_at"])


def _record_consent(alert: StockAlert, how: str) -> None:
    now = timezone.now()
    AlertConsent.objects.create(
        email=alert.email,
        variant=alert.variant,
        consented_at=now,
        notice_version=alert.notice_version,
        how_given=how,
    )
    alert.confirmed_at = now
    alert.expires_at = now + ALERT_TTL
    alert.save(update_fields=["confirmed_at", "expires_at"])


@transaction.atomic
def subscribe(
    *,
    variant_id: int,
    consent: bool,
    ip: str | None,
    user: User | None = None,
    email: str = "",
) -> str:
    """Ask to be emailed when the Variant is back. Returns `CONFIRMED` (a signed-in
    Shopper with a verified email) or `CONFIRMATION_SENT` (a confirm email is on its
    way, or already was: a repeat never reveals whether the address was known)."""
    if not shop_settings.get().stock_alerts_enabled:
        raise _unavailable()
    count_attempt("stock_alert", ip, Rate(*settings.STOCK_ALERT_RATE_LIMIT))
    if not consent:
        msg = "Bildirim için onay vermelisiniz."
        raise UnprocessableError(msg, fields={"consent": [msg]})
    signed_in = _verified(user)
    address = (user.email if signed_in else email).strip().lower()
    if not address:
        msg = "E-posta adresi gerekli."
        raise UnprocessableError(msg, fields={"email": [msg]})
    variant = _variant(variant_id)
    if not _sold_out(variant):
        raise _in_stock()
    notice = _notice_version()

    existing = _active(address).filter(variant=variant).first()
    if existing is not None:
        _repeat(existing, variant)
        return CONFIRMED if signed_in and existing.confirmed_at else CONFIRMATION_SENT
    # A lapsed row is dead weight that would collide with the unique constraint.
    StockAlert.objects.annotate(lowered=Lower("email")).filter(
        lowered=address,
        variant=variant,
    ).delete()
    if _active(address).count() >= MAX_ACTIVE_PER_EMAIL:
        msg = f"En fazla {MAX_ACTIVE_PER_EMAIL} bildirim isteyebilirsiniz."
        raise ConflictError(msg, code="stock_alert_limit")

    alert = StockAlert.objects.create(
        email=address,
        variant=variant,
        user=user if signed_in else None,
        notice_version=notice,
        confirm_token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + UNCONFIRMED_TTL,
    )
    if signed_in:
        _record_consent(alert, AlertConsent.HowGiven.SIGNED_IN)
        return CONFIRMED
    _send_confirm(alert, variant)
    return CONFIRMATION_SENT


def _repeat(alert: StockAlert, variant: Product) -> None:
    """Subscribing again: resend the confirm email at most hourly, else do nothing."""
    if alert.confirmed_at is not None:
        return
    sent = alert.confirm_sent_at
    if sent is None or timezone.now() - sent >= RESEND_AFTER:
        _send_confirm(alert, variant)


@transaction.atomic
def confirm(token: str) -> None:
    """The click in the confirm email: the Shopper's consent. Safe to click twice."""
    alert = (
        StockAlert.objects.select_for_update()
        .select_related("variant")
        .filter(confirm_token=token, expires_at__gt=timezone.now())
        .first()
    )
    if alert is None:
        raise NotFoundError
    if alert.confirmed_at is not None:
        return
    if not _sold_out(alert.variant):
        raise _in_stock()
    _record_consent(alert, AlertConsent.HowGiven.CONFIRM_LINK)


@transaction.atomic
def reject(token: str) -> None:
    """The reject link in every email: delete the live row, stamp the consent."""
    try:
        data = signing.loads(token, salt=REJECT_SALT)
        email, variant_id = data["e"], int(data["v"])
    except signing.BadSignature, KeyError, TypeError, ValueError:
        raise NotFoundError from None
    StockAlert.objects.annotate(lowered=Lower("email")).filter(
        lowered=email,
        variant_id=variant_id,
    ).delete()
    AlertConsent.objects.filter(
        email__iexact=email,
        variant_id=variant_id,
        rejected_at__isnull=True,
    ).update(rejected_at=timezone.now())


def purge() -> None:
    """Drop lapsed live rows and consent records older than 3 years."""
    now = timezone.now()
    StockAlert.objects.filter(expires_at__lte=now).delete()
    AlertConsent.objects.filter(consented_at__lte=now - CONSENT_TTL).delete()
