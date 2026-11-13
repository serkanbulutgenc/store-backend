"""Favorites: a Shopper's hearted Products (own model, ADR 0003)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import browse
from store_backend.services import reviews
from store_backend.shop.models import Favorite
from store_backend.users.models import User

if TYPE_CHECKING:
    from django.db.models import QuerySet

LIMIT = 200


def _published(user: User) -> QuerySet[Favorite]:
    """The Shopper's Favorites whose Product is on sale; the rest stay hidden."""
    return Favorite.objects.filter(
        user=user,
        product__profile__status=ProductStatus.PUBLISHED,
    )


@transaction.atomic
def add(user: User, product_id: int) -> None:
    """Heart a published Product. Hearting it again changes nothing; the 201st
    visible Favorite is refused (409 `favorites_limit`)."""
    if not ProductProfile.objects.filter(
        product_id=product_id,
        status=ProductStatus.PUBLISHED,
    ).exists():
        raise NotFoundError
    # Serialise this Shopper's adds so two taps cannot both slip under the limit.
    User.objects.select_for_update().get(pk=user.pk)
    if Favorite.objects.filter(user=user, product_id=product_id).exists():
        return
    if _published(user).count() >= LIMIT:
        msg = f"En fazla {LIMIT} ürünü favorilerinize ekleyebilirsiniz."
        raise ConflictError(msg, code="favorites_limit")
    Favorite.objects.create(user=user, product_id=product_id)


def remove(user: User, product_id: int) -> None:
    Favorite.objects.filter(user=user, product_id=product_id).delete()


def cards(user: User) -> list[dict]:
    """Storefront cards for the Shopper's published Favorites, newest first.
    Sold-out Products are included, flagged `sold_out`."""
    ids = list(_published(user).values_list("product_id", flat=True))
    if not ids:
        return []
    profiles = {
        p.product_id: p
        for p in browse.published().select_related("product").filter(product_id__in=ids)
    }
    variants = browse.variants_by_product(ids)
    photos = browse.photos_by_product(ids)
    ratings = reviews.ratings_for(ids)
    return [
        browse.product_card(profiles[pk], variants[pk], photos[pk], ratings[pk])
        for pk in ids
        if pk in profiles
    ]
