"""Collections as the Storefront shows them (GLOSSARY.md, ADR 0008).

A Collection is public while it is published and has at least one visible Product. A
visible Product is a published one: draft and archived Products are hidden, while a
sold-out one stays and is shown as sold out. Products come in the Owner's order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Exists
from django.db.models import OuterRef

from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import ProductStatus
from store_backend.services import browse
from store_backend.services import reviews

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from store_backend.catalog.models import ProductProfile

PATH_PREFIX = "/collections/"


def path(collection: Collection) -> str:
    """The Collection's page on the Storefront."""
    return f"{PATH_PREFIX}{collection.slug}"


def _visible_places() -> QuerySet[CollectionProduct]:
    return CollectionProduct.objects.filter(
        product__profile__status=ProductStatus.PUBLISHED,
    )


def is_public(collection: Collection) -> bool:
    """Published, with at least one visible Product."""
    return (
        collection.status == CollectionStatus.PUBLISHED
        and _visible_places().filter(collection=collection).exists()
    )


def public() -> QuerySet[Collection]:
    """Every public Collection."""
    return Collection.objects.filter(
        status=CollectionStatus.PUBLISHED,
    ).filter(Exists(_visible_places().filter(collection=OuterRef("pk"))))


def visible_products(collection: Collection) -> list[ProductProfile]:
    """The visible Products in the Owner's order."""
    profiles = browse.published().select_related("product")
    by_product = {
        p.product_id: p
        for p in profiles.filter(
            product__in=collection.places.values("product"),
        )
    }
    return [
        by_product[place.product_id]
        for place in collection.places.all()
        if place.product_id in by_product
    ]


def cover_image(collection: Collection) -> dict | None:
    """The cover image once its derivatives are ready, else None."""
    if collection.image_status != CategoryProfile.ImageStatus.READY:
        return None
    sizes = collection.image_derivative_urls()
    if not sizes:
        return None
    return {
        "alt": collection.image_alt or collection.name,
        "sizes": sorted(sizes, key=lambda s: s["width"]),
    }


def page(slug: str) -> dict:
    """What a Collection page shows. A Collection that is not published, or has no
    visible Product, is not found."""
    collection = Collection.objects.filter(
        slug=slug,
        status=CollectionStatus.PUBLISHED,
    ).first()
    if collection is None:
        raise NotFoundError
    profiles = visible_products(collection)
    if not profiles:
        raise NotFoundError
    ids = [p.product_id for p in profiles]
    variants = browse.variants_by_product(ids)
    photos = browse.photos_by_product(ids)
    ratings = reviews.ratings_for(ids)
    return {
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description,
        "image": cover_image(collection),
        "items": [
            browse.product_card(
                p,
                variants[p.product_id],
                photos[p.product_id],
                ratings[p.product_id],
            )
            for p in profiles
        ],
    }
