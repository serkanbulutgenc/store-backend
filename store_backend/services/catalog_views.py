"""Response view-models for the Dashboard catalog API: plain dicts that the Ninja
schemas validate. Handlers call these; they hold no HTTP or schema code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from store_backend.catalog.sizes import sizes_for
from store_backend.services import catalog
from store_backend.services import catalog_policy
from store_backend.services import media
from store_backend.services import stock

if TYPE_CHECKING:
    from oscar.apps.catalogue.models import Category
    from oscar.apps.catalogue.models import Product
    from oscar.apps.catalogue.models import ProductClass

    from store_backend.catalog.models import Colour
    from store_backend.catalog.models import ProductPhoto


def named(obj: Any) -> dict | None:
    return None if obj is None else {"id": obj.pk, "name": obj.name}


def colour_dict(c: Colour) -> dict:
    return {
        "id": c.pk,
        "name": c.name,
        "code": c.code,
        "hex_code": c.hex_code,
        "is_active": c.is_active,
    }


def category_dict(c: Category) -> dict:
    return {
        "id": c.pk,
        "name": c.name,
        "slug": c.slug,
        "parent_id": c.get_parent().pk if c.depth > 1 else None,
    }


def product_class_dict(pc: ProductClass, product_count: int | None = None) -> dict:
    return {
        "id": pc.pk,
        "name": pc.name,
        "size_scheme": pc.profile.size_scheme,
        "sizes": sizes_for(pc.profile.size_scheme),
        "default_kdv_rate": pc.profile.default_kdv_rate,
        "is_hygiene": pc.profile.is_hygiene,
        "default_desi": pc.profile.default_desi,
        "product_count": product_count,
    }


def variant_dict(v: Product) -> dict:
    vp = v.variant_profile
    records = list(v.stockrecords.all())
    record = records[0] if records else None
    on_hand = record.num_in_stock if record else 0
    allocated = (record.num_allocated or 0) if record else 0
    threshold = v.parent.profile.low_stock_threshold
    return {
        "id": v.pk,
        "product_id": v.parent_id,
        "sku": vp.sku,
        "barcode": vp.barcode,
        "colour": colour_dict(vp.colour),
        "size": vp.size,
        "price_override": vp.price_override,
        "stock": {"on_hand": on_hand, "allocated": allocated},
        "is_low_stock": stock.is_low(record, threshold),
    }


def product_dict(user: Any, product: Product) -> dict:
    """The full Product payload; Owner-only fields are omitted for other users."""
    profile = product.profile
    variants = (
        product.children.select_related("variant_profile__colour", "parent__profile")
        .prefetch_related("stockrecords")
        .order_by("variant_profile__colour__name", "pk")
    )
    supplier = profile.supplier
    payload: dict[str, Any] = {
        "id": product.pk,
        "title": product.title,
        "slug": profile.slug,
        "status": profile.status,
        "product_class": product_class_dict(product.product_class),
        "category": named(product.categories.first()),
        "brand": named(profile.brand),
        "shelf_price": profile.shelf_price,
        "old_price": profile.old_price,
        "kdv_rate": catalog.kdv_rate_for(product),
        "low_stock_threshold": profile.low_stock_threshold,
        "desi": profile.desi,
        "variants": [variant_dict(v) for v in variants],
        "cost_price": profile.cost_price,
        "supplier": None
        if supplier is None
        else {"id": supplier.pk, "name": supplier.name, "code": supplier.code},
        "supplier_code": profile.supplier_code,
    }
    return catalog_policy.omit_owner_fields(user, payload)


def list_dict(p: Product) -> dict:
    children = list(p.children.all())
    categories = list(p.categories.all())
    return {
        "id": p.pk,
        "title": p.title,
        "slug": p.profile.slug,
        "status": p.profile.status,
        "category": named(categories[0]) if categories else None,
        "shelf_price": p.profile.shelf_price,
        "variant_count": len(children),
        "stock_total": sum(
            r.num_in_stock for c in children for r in c.stockrecords.all()
        ),
    }


def low_stock_dict(v: Product) -> dict:
    return {
        "variant_id": v.pk,
        "product_id": v.parent_id,
        "product_title": v.parent.title,
        "sku": v.variant_profile.sku,
        "colour": v.variant_profile.colour.name,
        "size": v.variant_profile.size,
        "on_hand": v.free,
        "threshold": v.parent.profile.low_stock_threshold,
    }


def image_dict(image: ProductPhoto) -> dict:
    return {
        "id": image.pk,
        "product_id": image.product_id,
        "colour": colour_dict(image.colour) if image.colour else None,
        "sort_order": image.sort_order,
        "is_primary": image.is_primary,
        "status": image.status,
        "alt_text": image.effective_alt_text,
        "custom_alt_text": image.alt_text,
        "original_url": media.media_url(image.original),
        "width": image.width,
        "height": image.height,
        "derivatives": image.derivative_urls(),
        "error": image.error,
    }
