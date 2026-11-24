"""Basket Campaigns: an automatic offer once the basket reaches a minimum amount
(GLOSSARY: Basket Campaign, ADR 0011).

This module stores the rule and decides, for one basket, which one applies. It never
prices items (that is `campaign_pricing`) and never writes an Order (`order_creation`
freezes what `best` returned).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import NoReturn

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import BasketCampaign
from store_backend.services import audit
from store_backend.services import campaign_admin
from store_backend.services.pricing import HUNDRED
from store_backend.services.pricing import ZERO
from store_backend.services.pricing import kurus
from store_backend.shop.models import OrderBasketCampaign

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from store_backend.users.models import User

Benefit = BasketCampaign.Benefit

ALLOWED_BENEFITS = (Benefit.FIXED, Benefit.PERCENTAGE, Benefit.FREE_SHIPPING)
NAME_MAX = 200
MAX_AMOUNT = Decimal("99999999.99")
MIN_PERCENTAGE = Decimal(1)
MAX_PERCENTAGE = Decimal(99)


def _refuse(field: str, message: str) -> NoReturn:
    raise UnprocessableError(message, fields={field: [message]})


def _get(campaign_id: int) -> BasketCampaign:
    found = BasketCampaign.objects.filter(pk=campaign_id).first()
    if found is None:
        raise NotFoundError
    return found


# --- what the Shopper reads ----------------------------------------------------------


def _tl(amount: Decimal) -> str:
    """500 -> "500", 1250.5 -> "1.250,50": Turkish digits, cents only when needed."""
    whole, cents = divmod(int(amount * 100), 100)
    text = f"{whole:,}".replace(",", ".")
    return text if cents == 0 else f"{text},{cents:02d}"


def label_for(benefit: str, minimum: Decimal, value: Decimal | None) -> str:
    """The discount line's text, built from the rule: "500 ₺ üzeri 50 ₺ indirim"."""
    head = f"{_tl(minimum)} ₺ üzeri"
    if benefit == Benefit.FIXED and value is not None:
        return f"{head} {_tl(value)} ₺ indirim"
    if benefit == Benefit.PERCENTAGE and value is not None:
        return f"{head} %{_tl(value)} indirim"
    return f"{head} ücretsiz kargo"


# --- which one applies to a basket ---------------------------------------------------


@dataclass(frozen=True)
class Applied:
    """The Basket Campaign a basket gets: what it takes off the items (0 for free
    shipping), and whether it waives the shipping fee."""

    campaign_id: int
    name: str
    label: str
    benefit: str
    value: Decimal | None
    discount: Decimal
    free_shipping: bool


def active(now: datetime | None = None) -> QuerySet[BasketCampaign]:
    """Basket Campaigns in force at `now`: switched on, started, and not yet ended."""
    now = now or timezone.now()
    return BasketCampaign.objects.filter(is_active=True).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gt=now),
    )


def _discount_of(campaign: BasketCampaign, items_total: Decimal) -> Decimal:
    """What a discount `campaign` takes off `items_total`, never more than it."""
    if campaign.benefit == Benefit.PERCENTAGE:
        amount = kurus(items_total * (campaign.value or ZERO) / HUNDRED)
    else:
        amount = kurus(campaign.value or ZERO)
    return min(amount, items_total)


def _applied(campaign: BasketCampaign, discount: Decimal) -> Applied:
    return Applied(
        campaign_id=campaign.pk,
        name=campaign.name,
        label=label_for(campaign.benefit, campaign.minimum_amount, campaign.value),
        benefit=campaign.benefit,
        value=campaign.value,
        discount=discount,
        free_shipping=campaign.benefit == Benefit.FREE_SHIPPING,
    )


def applied(
    items_total: Decimal,
    shipping_fee: Decimal,
    *,
    now: datetime | None = None,
) -> list[Applied]:
    """The Basket Campaigns this basket gets: at most one discount and one free
    shipping, and both apply together when the basket qualifies for both.

    `items_total` is what the items cost at Campaign prices, before any Voucher;
    `shipping_fee` is what would be charged. Of several discounts only the one that
    takes most off applies (they never stack with each other), and a tie goes to the
    older one; free shipping applies only when there is a fee to waive. The discount
    comes first."""
    best_discount: tuple[BasketCampaign, Decimal] | None = None
    shipping: BasketCampaign | None = None
    for campaign in active(now).filter(minimum_amount__lte=items_total).order_by("pk"):
        if campaign.benefit == Benefit.FREE_SHIPPING:
            if shipping is None and shipping_fee > ZERO:
                shipping = campaign
            continue
        amount = _discount_of(campaign, items_total)
        if amount > ZERO and (best_discount is None or amount > best_discount[1]):
            best_discount = (campaign, amount)
    found = []
    if best_discount is not None:
        found.append(_applied(*best_discount))
    if shipping is not None:
        found.append(_applied(shipping, ZERO))
    return found


# --- the Owner's management ----------------------------------------------------------


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > NAME_MAX:
        _refuse("name", f"Kampanya adı 1-{NAME_MAX} karakter olmalı.")
    return cleaned


def _check_rule(
    benefit: str,
    minimum: Decimal,
    value: Decimal | None,
) -> Decimal | None:
    """Check the three together and return the value as it will be kept."""
    if benefit not in ALLOWED_BENEFITS:
        _refuse("benefit", "Geçersiz kampanya türü.")
    if not ZERO < minimum <= MAX_AMOUNT:
        _refuse("minimum_amount", "Sepet tutarı 0'dan büyük olmalı.")
    if benefit == Benefit.FREE_SHIPPING:
        if value is not None:
            _refuse("value", "Ücretsiz kargo için tutar girilmez.")
        return None
    if value is None:
        _refuse("value", "İndirim değeri gerekli.")
    if benefit == Benefit.PERCENTAGE:
        if not MIN_PERCENTAGE <= value <= MAX_PERCENTAGE:
            _refuse("value", "İndirim yüzdesi 1 ile 99 arasında olmalı.")
    elif not ZERO < value < minimum:
        _refuse("value", "İndirim tutarı sepet tutarından küçük olmalı.")
    return value.quantize(Decimal("0.01"))


def _check_schedule(starts_at: datetime | None, ends_at: datetime | None) -> None:
    if starts_at and ends_at and ends_at <= starts_at:
        _refuse("ends_at", "Bitiş, başlangıçtan sonra olmalı.")


def _snapshot(campaign: BasketCampaign) -> dict:
    """What the audit log keeps of a Basket Campaign: its settings as plain JSON."""
    return {
        "name": campaign.name,
        "minimum_amount": f"{campaign.minimum_amount:.2f}",
        "benefit": campaign.benefit,
        "value": None if campaign.value is None else f"{campaign.value:.2f}",
        "starts_at": campaign.starts_at.isoformat() if campaign.starts_at else None,
        "ends_at": campaign.ends_at.isoformat() if campaign.ends_at else None,
        "is_active": campaign.is_active,
    }


@transaction.atomic
def create(  # noqa: PLR0913
    actor: User,
    *,
    name: str,
    minimum_amount: Decimal,
    benefit: str,
    value: Decimal | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    is_active: bool = True,
) -> BasketCampaign:
    kept = _check_rule(benefit, minimum_amount, value)
    _check_schedule(starts_at, ends_at)
    campaign = BasketCampaign.objects.create(
        name=_clean_name(name),
        minimum_amount=minimum_amount.quantize(Decimal("0.01")),
        benefit=benefit,
        value=kept,
        starts_at=starts_at,
        ends_at=ends_at,
        is_active=is_active,
    )
    audit.record(
        actor,
        audit.Action.BASKET_CAMPAIGN_CREATED,
        target=campaign,
        summary=f"Sepet kampanyası oluşturuldu: {campaign.name}",
        after=_snapshot(campaign),
    )
    return campaign


@transaction.atomic
def edit(actor: User, campaign_id: int, **changes) -> BasketCampaign:
    """Change what was sent; anything left out stays."""
    campaign = _get(campaign_id)
    before = _snapshot(campaign)
    if "name" in changes:
        campaign.name = _clean_name(changes["name"])
    for field in ("minimum_amount", "benefit", "value", "starts_at", "ends_at"):
        if field in changes:
            setattr(campaign, field, changes[field])
    campaign.value = _check_rule(
        campaign.benefit,
        campaign.minimum_amount,
        campaign.value,
    )
    campaign.minimum_amount = campaign.minimum_amount.quantize(Decimal("0.01"))
    _check_schedule(campaign.starts_at, campaign.ends_at)
    campaign.save()
    audit.record(
        actor,
        audit.Action.BASKET_CAMPAIGN_EDITED,
        target=campaign,
        summary=f"Sepet kampanyası düzenlendi: {campaign.name}",
        before=before,
        after=_snapshot(campaign),
    )
    return campaign


@transaction.atomic
def activate(actor: User, campaign_id: int) -> BasketCampaign:
    return _switch(actor, campaign_id, on=True)


@transaction.atomic
def deactivate(actor: User, campaign_id: int) -> BasketCampaign:
    return _switch(actor, campaign_id, on=False)


def _switch(actor: User, campaign_id: int, *, on: bool) -> BasketCampaign:
    campaign = _get(campaign_id)
    if campaign.is_active != on:
        campaign.is_active = on
        campaign.save(update_fields=["is_active", "updated_at"])
        audit.record(
            actor,
            audit.Action.BASKET_CAMPAIGN_ACTIVATED
            if on
            else audit.Action.BASKET_CAMPAIGN_DEACTIVATED,
            target=campaign,
            summary=(
                f"Sepet kampanyası {'açıldı' if on else 'kapatıldı'}: {campaign.name}"
            ),
        )
    return campaign


def orders_using(campaign: BasketCampaign) -> int:
    """How many Orders were placed under `campaign`."""
    return OrderBasketCampaign.objects.filter(campaign=campaign).count()


@transaction.atomic
def delete(actor: User, campaign_id: int) -> None:
    campaign = _get(campaign_id)
    if used := orders_using(campaign):
        msg = f"Bu kampanya {used} siparişte kullanıldı; silinemez. Kapatabilirsiniz."
        raise ConflictError(msg, code="basket_campaign_used")
    audit.record(
        actor,
        audit.Action.BASKET_CAMPAIGN_DELETED,
        target=campaign,
        summary=f"Sepet kampanyası silindi: {campaign.name}",
        before=_snapshot(campaign),
    )
    campaign.delete()


def get(campaign_id: int) -> BasketCampaign:
    return _get(campaign_id)


def row(campaign: BasketCampaign) -> dict:
    return {
        "id": campaign.pk,
        "name": campaign.name,
        "label": label_for(campaign.benefit, campaign.minimum_amount, campaign.value),
        "minimum_amount": campaign.minimum_amount,
        "benefit": campaign.benefit,
        "value": campaign.value,
        "starts_at": campaign.starts_at,
        "ends_at": campaign.ends_at,
        "is_active": campaign.is_active,
        "status": campaign_admin.status(campaign),
        "orders": orders_using(campaign),
    }


def applied_views(found: list[Applied]) -> list[dict]:
    """The applied Basket Campaigns as the Shopper-facing APIs show them."""
    return [
        {"label": a.label, "discount": a.discount, "free_shipping": a.free_shipping}
        for a in found
    ]


def frozen(order) -> list[dict]:
    """What the Order was placed under, from its own frozen records, discount first:
    later edits of a Basket Campaign never change them."""
    rows = OrderBasketCampaign.objects.filter(order=order).order_by(
        "free_shipping",
        "pk",
    )
    return [
        {
            "label": r.label,
            "discount": r.discount,
            "free_shipping": r.free_shipping,
        }
        for r in rows
    ]


def listing(wanted: str | None = None) -> list[dict]:
    """The Basket Campaigns as table rows, newest first; `wanted` narrows by status."""
    if wanted is not None and wanted not in campaign_admin.STATUSES:
        _refuse("status", "Geçersiz durum.")
    campaigns = BasketCampaign.objects.all()
    if wanted:
        campaigns = campaign_admin.in_status(campaigns, wanted)
    return [row(c) for c in campaigns]
