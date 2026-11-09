"""Storefront product listing: filters, sorting, product cards and facet counts.

Filters come in two kinds. Product filters (category, text, brand, price) apply to the
Product. Variant filters (colour, size, in stock) must all be met by *one* Variant of
the Product, so "black" and "M" means a black Variant in size M. Facet counts for a
dimension respect every other active filter but not that dimension's own, so Shoppers
can see what widening a choice would add.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from django.db.models import Count
from django.db.models import Exists
from django.db.models import F
from django.db.models import Max
from django.db.models import Min
from django.db.models import OuterRef
from django.db.models import Subquery
from django.db.models import Value
from django.db.models.functions import Coalesce
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import ProductCategory

from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import VariantProfile
from store_backend.catalog.sizes import SIZES
from store_backend.services import search

if TYPE_CHECKING:
    from decimal import Decimal

    from django.db.models import QuerySet

PAGE_SIZE = 24
_SIZE_ORDER = {
    size: i
    for i, size in enumerate(dict.fromkeys(s for v in SIZES.values() for s in v))
}


@dataclass(frozen=True)
class Filters:
    category: str | None = None
    q: str = ""
    colours: list[int] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    brands: list[int] = field(default_factory=list)
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    in_stock: bool = True


# --- query building ------------------------------------------------------------


def _variants(
    *,
    colours: list[int] | None = None,
    sizes: list[str] | None = None,
    in_stock: bool = False,
) -> QuerySet[VariantProfile]:
    """Variants meeting the given variant filters (a blank filter is not applied)."""
    qs = VariantProfile.objects.annotate(
        available=F("product__stockrecords__num_in_stock")
        - Coalesce("product__stockrecords__num_allocated", Value(0)),
    )
    if colours:
        qs = qs.filter(colour_id__in=colours)
    if sizes:
        qs = qs.filter(size__in=sizes)
    if in_stock:
        qs = qs.filter(available__gt=0)
    return qs


def _price_from() -> Subquery:
    cheapest = (
        VariantProfile.objects.filter(parent=OuterRef("product_id"))
        .values("parent")
        .annotate(
            low=Min(Coalesce("price_override", F("parent__profile__shelf_price"))),
        )
        .values("low")
    )
    return Subquery(cheapest)


def _category_ids(slug: str) -> list[int]:
    category = Category.objects.filter(slug=slug).first()
    if category is None:
        raise NotFoundError
    return [category.pk, *category.get_descendants().values_list("pk", flat=True)]


def _published() -> QuerySet[ProductProfile]:
    return ProductProfile.objects.filter(status=ProductStatus.PUBLISHED).annotate(
        price_from=_price_from(),
    )


def _pool(f: Filters, *, skip: str | None = None) -> QuerySet[ProductProfile]:
    """Published Products meeting the Product filters, except the `skip` dimension."""
    qs = _published()
    if f.category:
        in_category = ProductCategory.objects.filter(
            product=OuterRef("product_id"),
            category_id__in=_category_ids(f.category),
        )
        qs = qs.filter(Exists(in_category))
    if f.q.strip():
        qs = search.search(qs, f.q)
    if f.brands and skip != "brand":
        qs = qs.filter(brand_id__in=f.brands)
    if skip != "price":
        if f.price_min is not None:
            qs = qs.filter(price_from__gte=f.price_min)
        if f.price_max is not None:
            qs = qs.filter(price_from__lte=f.price_max)
    return qs


def _with_matching_variant(
    qs: QuerySet[ProductProfile],
    f: Filters,
    *,
    colours: bool = True,
    sizes: bool = True,
) -> QuerySet[ProductProfile]:
    variants = _variants(
        colours=f.colours if colours else None,
        sizes=f.sizes if sizes else None,
        in_stock=f.in_stock,
    ).filter(parent=OuterRef("product_id"))
    return qs.filter(Exists(variants))


def matching(f: Filters) -> QuerySet[ProductProfile]:
    return _with_matching_variant(_pool(f), f)


# --- facets --------------------------------------------------------------------


def _colour_facet(f: Filters) -> list[dict]:
    pool = _pool(f).values("product_id")
    rows = (
        _variants(sizes=f.sizes, in_stock=f.in_stock)
        .filter(parent__in=pool)
        .values("colour_id")
        .annotate(n=Count("parent", distinct=True))
    )
    colours = Colour.objects.in_bulk([r["colour_id"] for r in rows])
    return sorted(
        (
            {
                "id": r["colour_id"],
                "name": colours[r["colour_id"]].name,
                "hex_code": colours[r["colour_id"]].hex_code,
                "count": r["n"],
            }
            for r in rows
        ),
        key=lambda c: c["name"],
    )


def _size_facet(f: Filters) -> list[dict]:
    pool = _pool(f).values("product_id")
    rows = (
        _variants(colours=f.colours, in_stock=f.in_stock)
        .filter(parent__in=pool)
        .values("size")
        .annotate(n=Count("parent", distinct=True))
    )
    return sorted(
        ({"size": r["size"], "count": r["n"]} for r in rows),
        key=lambda s: (_SIZE_ORDER.get(s["size"], len(_SIZE_ORDER)), s["size"]),
    )


def _brand_facet(f: Filters) -> list[dict]:
    pool = _with_matching_variant(_pool(f, skip="brand"), f)
    rows = pool.filter(brand__isnull=False).values("brand_id").annotate(n=Count("pk"))
    brands = Brand.objects.in_bulk([r["brand_id"] for r in rows])
    return sorted(
        (
            {"id": r["brand_id"], "name": brands[r["brand_id"]].name, "count": r["n"]}
            for r in rows
        ),
        key=lambda b: b["name"],
    )


def _price_range(f: Filters) -> dict:
    pool = _with_matching_variant(_pool(f, skip="price"), f)
    agg = pool.aggregate(low=Min("price_from"), high=Max("price_from"))
    return {"min": agg["low"], "max": agg["high"]}


# --- cards ---------------------------------------------------------------------


def _image(photos: list[ProductPhoto]) -> dict | None:
    ready = [p for p in photos if p.status == ProductPhoto.Status.READY]
    if not ready:
        return None
    chosen = next((p for p in ready if p.is_primary), ready[0])
    return {
        "alt": chosen.effective_alt_text,
        "sizes": sorted(chosen.derivative_urls(), key=lambda d: d["width"]),
    }


def _card(profile: ProductProfile, variants: list, photos: list) -> dict:
    in_stock = [v for v in variants if v["available"] > 0]
    shown = in_stock or variants
    seen: dict[int, Colour] = {}
    for v in shown:
        seen.setdefault(v["colour"].pk, v["colour"])
    price = (
        profile.price_from if profile.price_from is not None else profile.shelf_price
    )
    return {
        "id": profile.product_id,
        "slug": profile.slug,
        "title": profile.product.title,
        "price": price,
        # Only meaningful against the Product price, not a Variant's override.
        "old_price": profile.old_price if price == profile.shelf_price else None,
        "sold_out": not in_stock,
        "image": _image(photos),
        "colours": [
            {"id": c.pk, "name": c.name, "hex_code": c.hex_code}
            for c in sorted(seen.values(), key=lambda c: c.name)
        ],
    }


def variants_by_product(parent_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {pk: [] for pk in parent_ids}
    rows = (
        _variants()
        .filter(parent_id__in=parent_ids)
        .select_related("colour", "product")
        .order_by("colour__name", "pk")
    )
    for v in rows:
        out[v.parent_id].append(
            {
                "id": v.product_id,
                "colour": v.colour,
                "size": v.size,
                "sku": v.sku,
                "available": max(v.available or 0, 0),
                "price_override": v.price_override,
            },
        )
    return out


def photos_by_product(parent_ids: list[int]) -> dict[int, list[ProductPhoto]]:
    out: dict[int, list[ProductPhoto]] = {pk: [] for pk in parent_ids}
    for photo in ProductPhoto.objects.filter(product_id__in=parent_ids).select_related(
        "product",
        "colour",
    ):
        out[photo.product_id].append(photo)
    return out


def _order(qs: QuerySet[ProductProfile], sort: str, f: Filters) -> QuerySet:
    sold_out_last = Exists(
        _variants(in_stock=True).filter(parent=OuterRef("product_id")),
    )
    qs = qs.annotate(has_stock=sold_out_last)
    if sort == "relevance" and f.q.strip():
        return qs.order_by("-has_stock", "-relevance", "-product_id")
    if sort == "price_asc":
        return qs.order_by("-has_stock", "price_from", "-product_id")
    if sort == "price_desc":
        return qs.order_by("-has_stock", "-price_from", "-product_id")
    return qs.order_by("-has_stock", "-product_id")


def listing(f: Filters, *, sort: str = "newest", page: int = 1) -> dict:
    qs = _order(matching(f).select_related("product"), sort, f)
    total = qs.count()
    offset = (page - 1) * PAGE_SIZE
    profiles = list(qs[offset : offset + PAGE_SIZE])
    ids = [p.product_id for p in profiles]
    variants = variants_by_product(ids)
    photos = photos_by_product(ids)
    return {
        "items": [
            _card(p, variants[p.product_id], photos[p.product_id]) for p in profiles
        ],
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "facets": {
            "colours": _colour_facet(f),
            "sizes": _size_facet(f),
            "brands": _brand_facet(f),
            "price": _price_range(f),
        },
    }


# --- product page --------------------------------------------------------------


def category_tree() -> list[dict]:
    """Top-level Categories with their children, in tree order, for the menu."""
    roots: list[dict] = []
    for c in Category.objects.all().order_by("path"):
        node = {"name": c.name, "slug": c.slug, "children": []}
        if c.depth == 1:
            roots.append(node)
        else:
            roots[-1]["children"].append(node)
    return roots


def detail(slug: str) -> dict:
    """A published Product for its page; drafts and archived Products are not found."""
    profile = (
        _published()
        .select_related("product__product_class__profile", "brand")
        .filter(slug=slug)
        .first()
    )
    if profile is None:
        raise NotFoundError
    product = profile.product
    variants = variants_by_product([product.pk])[product.pk]
    photos = photos_by_product([product.pk])[product.pk]
    card = _card(profile, variants, photos)
    category = product.categories.first()
    crumbs = [*category.get_ancestors(), category] if category is not None else []
    threshold = profile.low_stock_threshold
    return {
        **card,
        "description": product.description or "",
        "brand": (
            {"id": profile.brand_id, "name": profile.brand.name}
            if profile.brand_id
            else None
        ),
        "breadcrumbs": [{"name": c.name, "slug": c.slug} for c in crumbs],
        "is_hygiene": product.product_class.profile.is_hygiene,
        "variants": [
            {
                "id": v["id"],
                "sku": v["sku"],
                "colour": {
                    "id": v["colour"].pk,
                    "name": v["colour"].name,
                    "hex_code": v["colour"].hex_code,
                },
                "size": v["size"],
                "price": (
                    v["price_override"]
                    if v["price_override"] is not None
                    else profile.shelf_price
                ),
                "available": v["available"],
                "in_stock": v["available"] > 0,
                # "son N adet" is shown at or below the low-stock threshold.
                "remaining": (
                    v["available"] if 0 < v["available"] <= threshold else None
                ),
            }
            for v in variants
        ],
        "photos": [
            {
                "id": p.pk,
                "colour_id": p.colour_id,
                "is_primary": p.is_primary,
                **(_image([p]) or {"alt": "", "sizes": []}),
            }
            for p in photos
            if p.status == ProductPhoto.Status.READY
        ],
    }
