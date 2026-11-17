"""The Owner's Collection management: create, edit, publish, unpublish and delete.

A Collection is a hand-picked, hand-ordered list of Products (see GLOSSARY.md). Editing
replaces the whole product list in one transaction, so a rejected edit changes nothing.
Staff access and pending changes arrive in later slices (ADR 0008).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.utils import timezone
from oscar.apps.catalogue.models import Product

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services.catalog import slugify_tr

if TYPE_CHECKING:
    from typing import NoReturn

    from store_backend.users.models import User

NAME_MAX = 200
DESCRIPTION_MAX = 500
PRODUCTS_MAX = 200


def _refuse(field: str, message: str) -> NoReturn:
    raise UnprocessableError(message, fields={field: [message]})


# --- checks -------------------------------------------------------------------------


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        _refuse("name", "Koleksiyon adı gerekli.")
    if len(cleaned) > NAME_MAX:
        _refuse("name", f"Ad en fazla {NAME_MAX} karakter olabilir.")
    return cleaned


def _clean_description(description: str) -> str:
    cleaned = description.strip()
    if len(cleaned) > DESCRIPTION_MAX:
        msg = f"Açıklama en fazla {DESCRIPTION_MAX} karakter olabilir."
        _refuse("description", msg)
    return cleaned


def _free_slug(base: str, *, ignore: int | None = None) -> str:
    """`base` if no other Collection has it, else the first `base-N` that is free."""
    taken = Collection.objects.exclude(pk=ignore)
    slug, number = base, 1
    while taken.filter(slug=slug).exists():
        number += 1
        slug = f"{base}-{number}"
    return slug


def _clean_slug(slug: str, *, ignore: int | None = None) -> str:
    cleaned = slugify_tr(slug)
    if not cleaned:
        _refuse("slug", "Geçerli bir adres girin.")
    if Collection.objects.exclude(pk=ignore).filter(slug=cleaned).exists():
        _refuse("slug", "Bu adres başka bir koleksiyonda kullanılıyor.")
    return cleaned


def _clean_product_ids(product_ids: list[int]) -> list[int]:
    if len(product_ids) > PRODUCTS_MAX:
        _refuse(
            "product_ids",
            f"Bir koleksiyonda en fazla {PRODUCTS_MAX} ürün olabilir.",
        )
    if len(set(product_ids)) != len(product_ids):
        _refuse("product_ids", "Aynı ürün bir kez eklenebilir.")
    found = set(
        Product.objects.filter(pk__in=product_ids, parent__isnull=True).values_list(
            "pk",
            flat=True,
        ),
    )
    if missing := [pk for pk in product_ids if pk not in found]:
        _refuse("product_ids", f"Ürün bulunamadı: {missing}.")
    return product_ids


# --- reading ------------------------------------------------------------------------


def _get(collection_id: int) -> Collection:
    collection = Collection.objects.filter(pk=collection_id).first()
    if collection is None:
        raise NotFoundError
    return collection


def _places(collection: Collection) -> list[CollectionProduct]:
    return list(
        collection.places.select_related("product__profile").order_by("position", "pk"),
    )


def _product_row(place: CollectionProduct) -> dict[str, Any]:
    profile = getattr(place.product, "profile", None)
    return {
        "id": place.product_id,
        "title": place.product.title,
        "slug": profile.slug if profile else None,
        "status": profile.status if profile else ProductStatus.DRAFT.value,
    }


def _base(collection: Collection) -> dict[str, Any]:
    return {
        "id": collection.pk,
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description,
        "status": collection.status,
        "published_at": collection.published_at,
    }


def view(collection: Collection) -> dict[str, Any]:
    """The Collection with its Products in order. `visible_product_count` is how many
    of them a Shopper would see: the approver is warned when it is zero."""
    rows = [_product_row(place) for place in _places(collection)]
    return {
        **_base(collection),
        "products": rows,
        "visible_product_count": sum(
            row["status"] == ProductStatus.PUBLISHED for row in rows
        ),
    }


def get(collection_id: int) -> dict[str, Any]:
    return view(_get(collection_id))


def listing(status: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in CollectionStatus.values:
        _refuse("status", "Bilinmeyen durum.")
    queryset = Collection.objects.all()
    if status:
        queryset = queryset.filter(status=status)
    return [
        {**_base(collection), "product_count": collection.places.count()}
        for collection in queryset
    ]


# --- writing ------------------------------------------------------------------------


def _snapshot(collection: Collection) -> dict[str, Any]:
    return {
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description,
        "status": collection.status,
        "product_ids": [place.product_id for place in _places(collection)],
    }


def _set_products(collection: Collection, product_ids: list[int]) -> None:
    collection.places.all().delete()
    CollectionProduct.objects.bulk_create(
        CollectionProduct(collection=collection, product_id=pk, position=position)
        for position, pk in enumerate(product_ids)
    )


@transaction.atomic
def create(
    actor: User,
    *,
    name: str,
    slug: str | None = None,
    description: str = "",
    product_ids: list[int] | None = None,
) -> Collection:
    """A new draft. Without `slug` it is made from the name and numbered if taken."""
    name = _clean_name(name)
    description = _clean_description(description)
    ids = _clean_product_ids(product_ids or [])
    if slug:
        final_slug = _clean_slug(slug)
    else:
        final_slug = _free_slug(slugify_tr(name) or "koleksiyon")
    collection = Collection.objects.create(
        name=name,
        slug=final_slug,
        description=description,
    )
    _set_products(collection, ids)
    audit.record(
        actor,
        audit.Action.COLLECTION_CREATED,
        target=collection,
        summary=f"Koleksiyon oluşturuldu: {collection.name}",
        after=_snapshot(collection),
    )
    return collection


@transaction.atomic
def edit(actor: User, collection_id: int, **changes: Any) -> Collection:
    """Change the fields given; leave the rest. `product_ids` replaces the list."""
    collection = Collection.objects.select_for_update().filter(pk=collection_id).first()
    if collection is None:
        raise NotFoundError
    before = _snapshot(collection)
    if "name" in changes:
        collection.name = _clean_name(changes["name"])
    if "description" in changes:
        collection.description = _clean_description(changes["description"])
    if "slug" in changes:
        collection.slug = _clean_slug(changes["slug"], ignore=collection.pk)
    ids = None
    if "product_ids" in changes:
        ids = _clean_product_ids(changes["product_ids"])
    collection.save()
    if ids is not None:
        _set_products(collection, ids)
    audit.record(
        actor,
        audit.Action.COLLECTION_EDITED,
        target=collection,
        summary=f"Koleksiyon düzenlendi: {collection.name}",
        before=before,
        after=_snapshot(collection),
    )
    return collection


def _move(  # noqa: PLR0913
    actor: User,
    collection_id: int,
    *,
    allowed_from: tuple[str, ...],
    to: CollectionStatus,
    action: audit.Action,
    summary: str,
) -> Collection:
    with transaction.atomic():
        collection = (
            Collection.objects.select_for_update().filter(pk=collection_id).first()
        )
        if collection is None:
            raise NotFoundError
        if collection.status not in allowed_from:
            msg = f"Koleksiyon bu durumdayken yapılamaz: {collection.status}."
            raise ConflictError(msg, code="collection_state")
        before = collection.status
        collection.status = to
        if to == CollectionStatus.PUBLISHED:
            collection.published_at = timezone.now()
        collection.save()
        audit.record(
            actor,
            action,
            target=collection,
            summary=f"{summary}: {collection.name}",
            before={"status": before},
            after={"status": to.value},
        )
    return collection


def publish(actor: User, collection_id: int) -> Collection:
    """Draft or unpublished to published; Owner-only (Staff submit instead)."""
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.DRAFT, CollectionStatus.UNPUBLISHED),
        to=CollectionStatus.PUBLISHED,
        action=audit.Action.COLLECTION_PUBLISHED,
        summary="Koleksiyon yayınlandı",
    )


def unpublish(actor: User, collection_id: int) -> Collection:
    """Published to unpublished: off the Storefront, kept."""
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.PUBLISHED,),
        to=CollectionStatus.UNPUBLISHED,
        action=audit.Action.COLLECTION_UNPUBLISHED,
        summary="Koleksiyon yayından kaldırıldı",
    )


@transaction.atomic
def delete(actor: User, collection_id: int) -> None:
    """Remove a Collection in any state. Its Products are never touched."""
    collection = _get(collection_id)
    audit.record(
        actor,
        audit.Action.COLLECTION_DELETED,
        target=collection,
        summary=f"Koleksiyon silindi: {collection.name}",
        before=_snapshot(collection),
    )
    collection.delete()
