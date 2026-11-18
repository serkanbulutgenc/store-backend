"""The Owner's Campaign management: create, edit, switch on and off, delete.

A Campaign is a rule (GLOSSARY.md, ADR 0009): this module stores it and reports what it
reaches, and never prices anything. `affected_product_ids` is the one place that turns
a Campaign's targets into Products, so the pricing slice reuses it.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import NoReturn

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductCategory

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Campaign
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import VariantProfile
from store_backend.services import audit
from store_backend.services.pricing import kurus

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from store_backend.users.models import User

STATUSES = ("active", "inactive", "scheduled", "ended")
NAME_MAX = 200
MIN_PERCENTAGE = Decimal(1)
MAX_PERCENTAGE = Decimal(99)


def _refuse(field: str, message: str) -> NoReturn:
    raise UnprocessableError(message, fields={field: [message]})


def _get(campaign_id: int) -> Campaign:
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        raise NotFoundError
    return campaign


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > NAME_MAX:
        msg = f"Kampanya adı 1-{NAME_MAX} karakter olmalı."
        _refuse("name", msg)
    return cleaned


def _check_percentage(percentage: Decimal) -> Decimal:
    if not MIN_PERCENTAGE <= percentage <= MAX_PERCENTAGE:
        msg = "İndirim yüzdesi 1 ile 99 arasında olmalı."
        _refuse("percentage", msg)
    return percentage.quantize(Decimal("0.01"))


def _check_schedule(starts_at: datetime | None, ends_at: datetime | None) -> None:
    if starts_at and ends_at and ends_at <= starts_at:
        msg = "Bitiş, başlangıçtan sonra olmalı."
        _refuse("ends_at", msg)


def _existing_ids(model, ids: list[int], field: str, *, parents_only=False) -> list:
    """The `ids` as rows of `model`, or a 422 naming the field if any is unknown."""
    rows = model.objects.filter(pk__in=set(ids))
    if parents_only:
        rows = rows.filter(structure=Product.PARENT)
    found = list(rows)
    if len(found) != len(set(ids)):
        msg = "Seçilen öğelerden biri bulunamadı."
        _refuse(field, msg)
    return found


def _set_targets(
    campaign: Campaign,
    product_ids: list[int],
    category_ids: list[int],
    collection_ids: list[int],
) -> None:
    from store_backend.catalog.models import Category  # noqa: PLC0415
    from store_backend.catalog.models import Collection  # noqa: PLC0415

    campaign.products.set(
        _existing_ids(Product, product_ids, "product_ids", parents_only=True),
    )
    campaign.categories.set(_existing_ids(Category, category_ids, "category_ids"))
    campaign.collections.set(
        _existing_ids(Collection, collection_ids, "collection_ids"),
    )


def _require_a_target(
    product_ids: list[int],
    category_ids: list[int],
    collection_ids: list[int],
) -> None:
    if not (product_ids or category_ids or collection_ids):
        msg = "En az bir ürün, kategori veya koleksiyon seçin."
        _refuse("product_ids", msg)


def _target_ids(campaign: Campaign) -> tuple[list[int], list[int], list[int]]:
    return (
        sorted(campaign.products.values_list("pk", flat=True)),
        sorted(campaign.categories.values_list("pk", flat=True)),
        sorted(campaign.collections.values_list("pk", flat=True)),
    )


@transaction.atomic
def create(  # noqa: PLR0913
    actor: User,
    *,
    name: str,
    percentage: Decimal,
    product_ids: list[int],
    category_ids: list[int],
    collection_ids: list[int],
    include_subcategories: bool = False,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    is_active: bool = True,
) -> Campaign:
    _require_a_target(product_ids, category_ids, collection_ids)
    _check_schedule(starts_at, ends_at)
    campaign = Campaign.objects.create(
        name=_clean_name(name),
        percentage=_check_percentage(percentage),
        include_subcategories=include_subcategories,
        starts_at=starts_at,
        ends_at=ends_at,
        is_active=is_active,
    )
    _set_targets(campaign, product_ids, category_ids, collection_ids)
    audit.record(
        actor,
        audit.Action.CAMPAIGN_CREATED,
        target=campaign,
        summary=f"Kampanya oluşturuldu: {campaign.name}",
        after=_snapshot(campaign),
    )
    return campaign


@transaction.atomic
def edit(actor: User, campaign_id: int, **changes) -> Campaign:
    """Change what was sent; anything left out stays. Target lists replace the old."""
    campaign = _get(campaign_id)
    before = _snapshot(campaign)
    targets = {
        key: changes.pop(key)
        for key in ("product_ids", "category_ids", "collection_ids")
        if changes.get(key) is not None
    }
    if "name" in changes:
        campaign.name = _clean_name(changes["name"])
    if "percentage" in changes:
        campaign.percentage = _check_percentage(changes["percentage"])
    for field in ("starts_at", "ends_at", "include_subcategories"):
        if field in changes:
            setattr(campaign, field, changes[field])
    _check_schedule(campaign.starts_at, campaign.ends_at)
    if targets:
        products, categories, collections = _target_ids(campaign)
        merged = {
            "product_ids": products,
            "category_ids": categories,
            "collection_ids": collections,
            **targets,
        }
        _require_a_target(**merged)
        campaign.save()
        _set_targets(campaign, **merged)
    else:
        campaign.save()
    audit.record(
        actor,
        audit.Action.CAMPAIGN_EDITED,
        target=campaign,
        summary=f"Kampanya düzenlendi: {campaign.name}",
        before=before,
        after=_snapshot(campaign),
    )
    return campaign


@transaction.atomic
def activate(actor: User, campaign_id: int) -> Campaign:
    return _switch(actor, campaign_id, on=True)


@transaction.atomic
def deactivate(actor: User, campaign_id: int) -> Campaign:
    return _switch(actor, campaign_id, on=False)


def _switch(actor: User, campaign_id: int, *, on: bool) -> Campaign:
    campaign = _get(campaign_id)
    if campaign.is_active != on:
        campaign.is_active = on
        campaign.save(update_fields=["is_active", "updated_at"])
        audit.record(
            actor,
            audit.Action.CAMPAIGN_ACTIVATED
            if on
            else audit.Action.CAMPAIGN_DEACTIVATED,
            target=campaign,
            summary=f"Kampanya {'açıldı' if on else 'kapatıldı'}: {campaign.name}",
        )
    return campaign


def orders_using(campaign: Campaign) -> int:
    """How many Orders were bought under `campaign`. Orders do not carry a Campaign
    yet (the Order slice adds it), so none do; delete's refusal is already wired."""
    return 0


@transaction.atomic
def delete(actor: User, campaign_id: int) -> None:
    campaign = _get(campaign_id)
    if used := orders_using(campaign):
        msg = f"Bu kampanya {used} siparişte kullanıldı; silinemez. Kapatabilirsiniz."
        raise ConflictError(msg, code="campaign_used")
    audit.record(
        actor,
        audit.Action.CAMPAIGN_DELETED,
        target=campaign,
        summary=f"Kampanya silindi: {campaign.name}",
        before=_snapshot(campaign),
    )
    campaign.delete()


def status(campaign: Campaign) -> str:
    """The first that applies: inactive (the switch), scheduled, ended, else active."""
    now = timezone.now()
    if not campaign.is_active:
        return "inactive"
    if campaign.starts_at and campaign.starts_at > now:
        return "scheduled"
    if campaign.ends_at and campaign.ends_at <= now:
        return "ended"
    return "active"


def affected_product_ids(campaign: Campaign) -> set[int]:
    """The Products (parents) the Campaign's targets reach: the chosen Products, those
    in a chosen Category (and its subcategories if flagged) or a chosen Collection."""
    ids = set(campaign.products.values_list("pk", flat=True))
    categories = list(campaign.categories.all())
    if campaign.include_subcategories:
        tree: set[int] = set()
        for category in categories:
            tree.update(c.pk for c in category.get_descendants_and_self())
        category_ids = tree
    else:
        category_ids = {c.pk for c in categories}
    ids.update(
        ProductCategory.objects.filter(category_id__in=category_ids).values_list(
            "product_id",
            flat=True,
        ),
    )
    ids.update(
        CollectionProduct.objects.filter(
            collection__in=campaign.collections.all(),
        ).values_list("product_id", flat=True),
    )
    return ids


def below_cost(campaign: Campaign) -> list[dict]:
    """Affected Products where the Campaign price, for any Variant, is under the cost
    price (compared as entered). A warning for the Owner; saving is never blocked."""
    profiles = ProductProfile.objects.filter(
        product_id__in=affected_product_ids(campaign),
        cost_price__isnull=False,
    ).select_related("product")
    overrides: dict[int, list[Decimal]] = {}
    for parent_id, price in VariantProfile.objects.filter(
        parent_id__in=[p.product_id for p in profiles],
        price_override__isnull=False,
    ).values_list("parent_id", "price_override"):
        overrides.setdefault(parent_id, []).append(price)
    factor = (Decimal(100) - campaign.percentage) / Decimal(100)
    found = []
    for profile in profiles:
        prices = [profile.shelf_price, *overrides.get(profile.product_id, [])]
        lowest = kurus(min(prices) * factor)
        if lowest < profile.cost_price:
            found.append(
                {
                    "product_id": profile.product_id,
                    "title": profile.product.title,
                    "campaign_price": lowest,
                    "cost_price": profile.cost_price,
                },
            )
    return sorted(found, key=lambda row: (row["title"], row["product_id"]))


def _snapshot(campaign: Campaign) -> dict:
    """What the audit log keeps of a Campaign: its settings as plain JSON."""
    products, categories, collections = _target_ids(campaign)
    return {
        "name": campaign.name,
        "percentage": f"{campaign.percentage:.2f}",
        "starts_at": campaign.starts_at.isoformat() if campaign.starts_at else None,
        "ends_at": campaign.ends_at.isoformat() if campaign.ends_at else None,
        "is_active": campaign.is_active,
        "include_subcategories": campaign.include_subcategories,
        "product_ids": products,
        "category_ids": categories,
        "collection_ids": collections,
    }


def _row(campaign: Campaign) -> dict:
    return {
        "id": campaign.pk,
        "name": campaign.name,
        "percentage": campaign.percentage,
        "starts_at": campaign.starts_at,
        "ends_at": campaign.ends_at,
        "is_active": campaign.is_active,
        "status": status(campaign),
    }


def view(campaign: Campaign) -> dict:
    """The Campaign as the Dashboard form shows it, with the below-cost warning."""
    return {
        **_row(campaign),
        "include_subcategories": campaign.include_subcategories,
        "products": [
            {"id": p.pk, "name": p.title} for p in campaign.products.order_by("title")
        ],
        "categories": [
            {"id": c.pk, "name": c.name} for c in campaign.categories.order_by("name")
        ],
        "collections": [
            {"id": c.pk, "name": c.name} for c in campaign.collections.order_by("name")
        ],
        "affected_products": len(affected_product_ids(campaign)),
        "below_cost": below_cost(campaign),
    }


def get(campaign_id: int) -> Campaign:
    return _get(campaign_id)


def _in_status(campaigns: QuerySet[Campaign], wanted: str) -> QuerySet[Campaign]:
    now = timezone.now()
    live = Q(is_active=True)
    started = Q(starts_at__isnull=True) | Q(starts_at__lte=now)
    not_ended = Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    if wanted == "inactive":
        return campaigns.filter(is_active=False)
    if wanted == "scheduled":
        return campaigns.filter(live, starts_at__gt=now)
    if wanted == "ended":
        return campaigns.filter(live, started, ends_at__lte=now)
    return campaigns.filter(live, started, not_ended)


def listing(wanted: str | None = None) -> list[dict]:
    """The Campaigns as table rows, newest first; `wanted` narrows by status."""
    if wanted is not None and wanted not in STATUSES:
        msg = "Geçersiz durum."
        _refuse("status", msg)
    campaigns = Campaign.objects.all()
    if wanted:
        campaigns = _in_status(campaigns, wanted)
    return [_row(c) for c in campaigns]
