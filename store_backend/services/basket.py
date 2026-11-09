"""The Shopper's basket over Oscar's Basket model.

The basket never reserves stock: it only checks that the units asked for exist right
now, on add and on quantity change. Prices are always the live shelf prices, so a basket
that sat for days shows today's numbers. Checkout re-validates everything at placement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from oscar.apps.catalogue.models import Product
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import ProductStatus
from store_backend.services import browse
from store_backend.services import catalog
from store_backend.services import pricing

if TYPE_CHECKING:
    from oscar.apps.basket.models import Basket

MAX_QUANTITY = 99


def _available(variant: Product) -> int:
    record = StockRecord.objects.filter(product=variant).first()
    if record is None:
        return 0
    return max(record.num_in_stock - (record.num_allocated or 0), 0)


def _purchasable_variant(variant_id: int) -> Product:
    """A Variant of a published Product, or 404 (drafts and archived stay invisible)."""
    variant = (
        Product.objects.filter(
            pk=variant_id,
            structure=Product.CHILD,
            parent__profile__status=ProductStatus.PUBLISHED,
        )
        .select_related("variant_profile", "parent__profile")
        .first()
    )
    if variant is None:
        raise NotFoundError
    return variant


def _check_stock(variant: Product, wanted: int) -> None:
    available = _available(variant)
    if available == 0:
        msg = "Bu ürün tükendi."
        raise ConflictError(msg, code="sold_out", fields={"quantity": [msg]})
    if wanted > available:
        msg = f"Stokta en fazla {available} adet var."
        raise ConflictError(msg, code="insufficient_stock", fields={"quantity": [msg]})


@transaction.atomic
def add(basket: Basket, variant_id: int, quantity: int) -> None:
    """Add units; a line for the same Variant grows and the new total is checked."""
    variant = _purchasable_variant(variant_id)
    existing = basket.lines.filter(product=variant).first() if basket.id else None
    wanted = quantity + (existing.quantity if existing else 0)
    if wanted > MAX_QUANTITY:
        msg = f"Bir üründen en fazla {MAX_QUANTITY} adet alınabilir."
        raise ConflictError(msg, code="quantity_limit", fields={"quantity": [msg]})
    _check_stock(variant, wanted)
    basket.add_product(variant, quantity)


def _line(basket: Basket, line_id: int):
    line = (
        basket.lines.filter(pk=line_id).select_related("product").first()
        if basket.id
        else None
    )
    if line is None:
        raise NotFoundError
    return line


@transaction.atomic
def set_quantity(basket: Basket, line_id: int, quantity: int) -> None:
    line = _line(basket, line_id)
    variant = _purchasable_variant(line.product_id)
    _check_stock(variant, quantity)
    line.quantity = quantity
    line.save(update_fields=["quantity"])


def remove(basket: Basket, line_id: int) -> None:
    _line(basket, line_id).delete()


def _problem(variant: Product | None, quantity: int, available: int) -> str | None:
    if variant is None:
        return "unavailable"
    if available == 0:
        return "sold_out"
    if quantity > available:
        return "insufficient_stock"
    return None


def view(basket: Basket) -> dict:
    """The basket as the storefront shows it: lines with live prices, and the subtotal.

    A line whose Product was unpublished or sold out stays visible with a `problem`, so
    the Shopper can see and remove it; checkout refuses to proceed while any remain.
    """
    lines = (
        list(
            basket.lines.select_related(
                "product__variant_profile__colour",
                "product__parent__profile__brand",
                "product__parent__product_class__profile",
            ).order_by("date_created", "pk"),
        )
        if basket.id
        else []
    )
    parent_ids = list({line.product.parent_id for line in lines})
    photos = browse.photos_by_product(parent_ids)
    out = []
    subtotal = pricing.ZERO
    for line in lines:
        variant = line.product
        profile = variant.variant_profile
        parent = variant.parent
        published = parent.profile.status == ProductStatus.PUBLISHED
        available = _available(variant)
        unit = catalog.shelf_price_for(variant)
        total = pricing.kurus(unit) * line.quantity
        subtotal += total
        coloured = [p for p in photos[parent.pk] if p.colour_id == profile.colour_id]
        out.append(
            {
                "id": line.pk,
                "variant_id": variant.pk,
                "sku": profile.sku,
                "title": parent.title,
                "slug": parent.profile.slug,
                "colour": {
                    "name": profile.colour.name,
                    "hex_code": profile.colour.hex_code,
                },
                "size": profile.size,
                "image": browse._image(coloured or photos[parent.pk]),  # noqa: SLF001
                "quantity": line.quantity,
                "unit_price": unit,
                "line_total": total,
                "available": available,
                "is_hygiene": parent.product_class.profile.is_hygiene,
                "problem": _problem(
                    variant if published else None,
                    line.quantity,
                    available,
                ),
            },
        )
    return {
        "lines": out,
        "item_count": sum(line["quantity"] for line in out),
        "subtotal": subtotal,
        "has_problems": any(line["problem"] for line in out),
    }
