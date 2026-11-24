"""The Campaign price, computed at read time (GLOSSARY: Campaign, ADR 0009).

Every price surface that shows or sorts by price goes through this module, so the
Storefront and the Dashboard agree on which Campaign wins and what it takes off.
`campaign_admin.affected_product_ids` is the one place a Campaign's targets become
Products.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Case
from django.db.models import DecimalField
from django.db.models import Q
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Round
from django.utils import timezone

from store_backend.catalog.models import Campaign
from store_backend.services import campaign_admin
from store_backend.services import catalog
from store_backend.services.pricing import HUNDRED
from store_backend.services.pricing import kurus

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from django.db.models import Expression
    from django.db.models import QuerySet
    from oscar.apps.catalogue.models import Product


@dataclass(frozen=True)
class Winner:
    """The Campaign pricing a Product: the largest discount of those in force."""

    campaign_id: int
    name: str
    percentage: Decimal


def discounted(shelf: Decimal, percentage: Decimal) -> Decimal:
    """`shelf` (KDV-inclusive) less `percentage`, rounded half-up to kuruş."""
    return kurus(shelf * (HUNDRED - percentage) / HUNDRED)


@dataclass(frozen=True)
class Priced:
    """What a Variant costs right now and why: `unit` is KDV-inclusive whole kuruş, the
    `shelf` price less the `winner`'s percentage, or the Shelf price if none."""

    unit: Decimal
    shelf: Decimal
    winner: Winner | None


def priced(
    variants: Iterable[Product],
    *,
    now: datetime | None = None,
) -> dict[int, Priced]:
    """Each Variant's price right now, with the Campaign behind it. Basket, quote and
    checkout price through this, so they never disagree with the Storefront. The
    Variants need `variant_profile` and `parent__profile` loaded."""
    variants = list(variants)
    won = winners({v.parent_id for v in variants}, now=now)
    out = {}
    for variant in variants:
        shelf = kurus(catalog.shelf_price_for(variant))
        winner = won.get(variant.parent_id)
        unit = shelf if winner is None else discounted(shelf, winner.percentage)
        out[variant.pk] = Priced(unit, shelf, winner)
    return out


def unit_prices(
    variants: Iterable[Product],
    *,
    now: datetime | None = None,
) -> dict[int, Decimal]:
    """Just the price of each Variant, from `priced`."""
    return {pk: p.unit for pk, p in priced(variants, now=now).items()}


def unit_price(variant: Product, *, now: datetime | None = None) -> Decimal:
    return unit_prices([variant], now=now)[variant.pk]


def active(now: datetime | None = None) -> QuerySet[Campaign]:
    """Campaigns in force at `now`: switched on, started, and not yet ended. This is
    `campaign_admin.status(...) == "active"` as a query."""
    now = now or timezone.now()
    return Campaign.objects.filter(is_active=True).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gt=now),
    )


def winners(
    product_ids: Iterable[int] | None = None,
    *,
    now: datetime | None = None,
) -> dict[int, Winner]:
    """The winning Campaign of each of the given Products (all, if none are named)
    that has one. Ties go to the older Campaign."""
    wanted = None if product_ids is None else set(product_ids)
    best: dict[int, Winner] = {}
    for campaign in active(now).order_by("pk"):
        reached = campaign_admin.affected_product_ids(campaign, among=wanted)
        for product_id in reached if wanted is None else reached & wanted:
            current = best.get(product_id)
            if current is None or campaign.percentage > current.percentage:
                best[product_id] = Winner(
                    campaign.pk,
                    campaign.name,
                    campaign.percentage,
                )
    return best


def winner_dict(winner: Winner | None) -> dict | None:
    """The winner as the Dashboard shows it on a Product."""
    if winner is None:
        return None
    return {
        "id": winner.campaign_id,
        "name": winner.name,
        "percentage": winner.percentage,
    }


def percentage_sql(product_id: str = "product_id", *, now: datetime | None = None):
    """The winning percentage as a query expression over a Product's id column (0 when
    no Campaign reaches it), so listings and price filters stay set-based. The rules
    are resolved in a few queries however many Products the listing holds."""
    by_percentage: dict[Decimal, list[int]] = {}
    for product, winner in winners(now=now).items():
        by_percentage.setdefault(winner.percentage, []).append(product)
    return Case(
        *(
            When(**{f"{product_id}__in": ids}, then=Value(percentage))
            for percentage, ids in by_percentage.items()
        ),
        default=Value(Decimal(0)),
        output_field=DecimalField(max_digits=5, decimal_places=2),
    )


def discounted_sql(shelf: Expression, percentage: Expression):
    """`discounted` in SQL: numeric rounding is half away from zero, i.e. half-up."""
    return Round(shelf * (Value(HUNDRED) - percentage) / Value(HUNDRED), 2)


__all__ = [
    "Priced",
    "Winner",
    "active",
    "discounted",
    "discounted_sql",
    "percentage_sql",
    "priced",
    "unit_price",
    "unit_prices",
    "winner_dict",
    "winners",
]
