"""Collection management: create, edit, submit, approve, publish, unpublish, delete.

A Collection is a hand-picked, hand-ordered list of Products (see GLOSSARY.md). Editing
replaces the whole product list in one transaction, so a rejected edit changes nothing.
Staff edit drafts and submit them; the Owner approves or rejects (ADR 0008). A Staff
edit to a published Collection is held as a pending change (at most one) until the
Owner approves or rejects it; the live version is untouched meanwhile.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from oscar.apps.catalogue.models import Product

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionChange
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import media
from store_backend.services.catalog import slugify_tr
from store_backend.shop.models import MenuItem
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from typing import NoReturn

    from django.core.files.uploadedfile import UploadedFile

    from store_backend.users.models import User

NAME_MAX = 200
DESCRIPTION_MAX = 500
PRODUCTS_MAX = 200
REASON_MAX = 500


def _is_owner(actor: User) -> bool:
    """Whether `actor` may publish; Staff hold only `edit_collections`."""
    return actor.has_perm(Perm.MANAGE_COLLECTIONS.permission_name)


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
    collection = (
        Collection.objects.select_related("submitted_by")
        .filter(pk=collection_id)
        .first()
    )
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


def _person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.pk, "name": user.name or user.email}


def _base(collection: Collection) -> dict[str, Any]:
    return {
        "id": collection.pk,
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description,
        "status": collection.status,
        "published_at": collection.published_at,
        "submitted_by": _person(collection.submitted_by),
        "submitted_at": collection.submitted_at,
        "rejection_reason": collection.rejection_reason,
    }


def _change_view(collection: Collection) -> dict[str, Any] | None:
    change = getattr(collection, "pending_change", None)
    if change is None:
        return None
    products = {
        p.pk: p
        for p in Product.objects.filter(pk__in=change.product_ids).select_related(
            "profile",
        )
    }
    rows = []
    for pk in change.product_ids:
        product = products.get(pk)
        if product is None:
            continue  # deleted since; approval will refuse until the change is fixed
        profile = getattr(product, "profile", None)
        rows.append(
            {
                "id": pk,
                "title": product.title,
                "slug": profile.slug if profile else None,
                "status": profile.status if profile else ProductStatus.DRAFT.value,
            },
        )
    return {
        "name": change.name,
        "slug": change.slug,
        "description": change.description,
        "products": rows,
        "submitted_by": _person(change.submitted_by),
        "submitted_at": change.submitted_at,
        "updated_at": change.updated_at,
    }


def image_view(collection: Collection) -> dict[str, Any] | None:
    if not collection.image_original:
        return None
    return {
        "status": collection.image_status,
        "alt_text": collection.image_alt,
        "original_url": media.media_url(collection.image_original),
        "width": collection.image_width,
        "height": collection.image_height,
        "derivatives": collection.image_derivative_urls(),
        "error": collection.image_error,
    }


def view(collection: Collection) -> dict[str, Any]:
    """The Collection with its Products in order. `visible_product_count` is how many
    of them a Shopper would see: the approver is warned when it is zero."""
    rows = [_product_row(place) for place in _places(collection)]
    return {
        **_base(collection),
        "pending_change": _change_view(collection),
        "image": image_view(collection),
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
    queryset = Collection.objects.select_related("submitted_by", "pending_change")
    if status:
        queryset = queryset.filter(status=status)
    return [
        {
            **_base(collection),
            "product_count": collection.places.count(),
            "has_pending_change": hasattr(collection, "pending_change"),
        }
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


def _refuse_staff_edit(collection: Collection) -> NoReturn:
    if collection.status == CollectionStatus.PENDING:
        msg = "Onay bekleyen koleksiyon düzenlenemez; önce geri çekin."
        raise ConflictError(msg, code="collection_state")
    msg = "Kaldırılan koleksiyonu yalnızca sahip düzenleyebilir."
    raise ForbiddenError(msg, code="collection_owner_only")


def _clean_changes(
    changes: dict[str, Any],
    *,
    ignore: int,
) -> dict[str, Any]:
    """Validate the fields given; the result has only those keys."""
    cleaned: dict[str, Any] = {}
    if "name" in changes:
        cleaned["name"] = _clean_name(changes["name"])
    if "description" in changes:
        cleaned["description"] = _clean_description(changes["description"])
    if "slug" in changes:
        cleaned["slug"] = _clean_slug(changes["slug"], ignore=ignore)
    if "product_ids" in changes:
        cleaned["product_ids"] = _clean_product_ids(changes["product_ids"])
    return cleaned


def _stage_change(
    actor: User,
    collection: Collection,
    cleaned: dict[str, Any],
) -> None:
    """Hold a Staff edit of a published Collection: create the one pending change, or
    amend it. The live Collection is not touched."""
    change = getattr(collection, "pending_change", None)
    before = _change_snapshot(change) if change else None
    if change is None:
        change = CollectionChange(
            collection=collection,
            name=collection.name,
            slug=collection.slug,
            description=collection.description,
            product_ids=[place.product_id for place in _places(collection)],
        )
    for field, value in cleaned.items():
        setattr(change, field, value)
    change.submitted_by = actor
    change.save()
    collection.pending_change = change
    collection.rejection_reason = ""
    collection.save(update_fields=["rejection_reason", "updated_at"])
    audit.record(
        actor,
        audit.Action.COLLECTION_CHANGE_SUBMITTED,
        target=collection,
        summary=(
            f"Koleksiyon değişikliği "
            f"{'güncellendi' if before else 'onaya gönderildi'}: {collection.name}"
        ),
        before=before,
        after=_change_snapshot(change),
    )


def _change_snapshot(change: CollectionChange) -> dict[str, Any]:
    return {
        "name": change.name,
        "slug": change.slug,
        "description": change.description,
        "product_ids": list(change.product_ids),
    }


def _locked(collection_id: int) -> Collection:
    collection = (
        Collection.objects.select_for_update(of=("self",))
        .select_related("pending_change", "submitted_by")
        .filter(pk=collection_id)
        .first()
    )
    if collection is None:
        raise NotFoundError
    return collection


@transaction.atomic
def edit(actor: User, collection_id: int, **changes: Any) -> Collection:
    """Change the fields given; leave the rest. `product_ids` replaces the list.

    The Owner edits any Collection directly, except a published one that has a pending
    change (approve or reject that first). Staff edit drafts directly; their edit of a
    published Collection becomes, or amends, its pending change."""
    collection = _locked(collection_id)
    published = collection.status == CollectionStatus.PUBLISHED
    if _is_owner(actor):
        if published and hasattr(collection, "pending_change"):
            msg = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
            raise ConflictError(msg, code="collection_has_pending_change")
    elif published:
        cleaned = _clean_changes(changes, ignore=collection.pk)
        _stage_change(actor, collection, cleaned)
        return _get(collection_id)
    elif collection.status != CollectionStatus.DRAFT:
        _refuse_staff_edit(collection)
    before = _snapshot(collection)
    cleaned = _clean_changes(changes, ignore=collection.pk)
    ids = cleaned.pop("product_ids", None)
    for field, value in cleaned.items():
        setattr(collection, field, value)
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


@transaction.atomic
def edit_change(actor: User, collection_id: int, **changes: Any) -> Collection:
    """The Owner amends a pending change before approving it."""
    collection = _locked(collection_id)
    change = getattr(collection, "pending_change", None)
    if change is None:
        msg = "Bekleyen bir değişiklik yok."
        raise ConflictError(msg, code="collection_no_pending_change")
    before = _change_snapshot(change)
    for field, value in _clean_changes(changes, ignore=collection.pk).items():
        setattr(change, field, value)
    change.save()
    audit.record(
        actor,
        audit.Action.COLLECTION_CHANGE_SUBMITTED,
        target=collection,
        summary=f"Bekleyen değişiklik düzenlendi: {collection.name}",
        before=before,
        after=_change_snapshot(change),
    )
    return collection


def _has_change(collection_id: int) -> bool:
    return CollectionChange.objects.filter(
        collection_id=collection_id,
        collection__status=CollectionStatus.PUBLISHED,
    ).exists()


@transaction.atomic
def _approve_change(actor: User, collection_id: int) -> Collection:
    collection = _locked(collection_id)
    change = collection.pending_change
    # Revalidate: a slug or Product may have gone since Staff made the edit.
    cleaned = _clean_changes(_change_snapshot(change), ignore=collection.pk)
    before = _snapshot(collection)
    ids = cleaned.pop("product_ids")
    for field, value in cleaned.items():
        setattr(collection, field, value)
    collection.rejection_reason = ""
    collection.save()
    _set_products(collection, ids)
    change.delete()
    audit.record(
        actor,
        audit.Action.COLLECTION_CHANGE_APPROVED,
        target=collection,
        summary=f"Koleksiyon değişikliği onaylandı: {collection.name}",
        before=before,
        after=_snapshot(collection),
    )
    return _get(collection_id)


@transaction.atomic
def _reject_change(actor: User, collection_id: int, reason: str) -> Collection:
    collection = _locked(collection_id)
    change = collection.pending_change
    dropped = _change_snapshot(change)
    # The change is gone, so its submitter reads the reason on the Collection.
    collection.submitted_by = change.submitted_by
    collection.submitted_at = change.updated_at
    collection.rejection_reason = reason
    collection.save()
    change.delete()
    audit.record(
        actor,
        audit.Action.COLLECTION_CHANGE_REJECTED,
        target=collection,
        summary=f"Koleksiyon değişikliği reddedildi: {collection.name}",
        before=dropped,
        after={"reason": reason} if reason else None,
    )
    return _get(collection_id)


def _move(  # noqa: PLR0913
    actor: User,
    collection_id: int,
    *,
    allowed_from: tuple[str, ...],
    to: CollectionStatus,
    action: audit.Action,
    summary: str,
    only_submitter: bool = False,
    reason: str = "",
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
        if only_submitter and collection.submitted_by_id != actor.pk:
            msg = "Yalnızca gönderen kişi geri çekebilir."
            raise ForbiddenError(msg, code="collection_not_submitter")
        before = collection.status
        collection.status = to
        if to == CollectionStatus.PUBLISHED:
            collection.published_at = timezone.now()
        if action == audit.Action.COLLECTION_SUBMITTED:
            collection.submitted_by = actor
            collection.submitted_at = timezone.now()
            collection.rejection_reason = ""
        if action == audit.Action.COLLECTION_REJECTED:
            collection.rejection_reason = reason
        collection.save()
        audit.record(
            actor,
            action,
            target=collection,
            summary=f"{summary}: {collection.name}",
            before={"status": before},
            after={
                "status": to.value,
                **({"reason": reason} if reason else {}),
            },
        )
    return collection


def submit(actor: User, collection_id: int) -> Collection:
    """Draft to pending approval, for the Owner to approve or reject."""
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.DRAFT,),
        to=CollectionStatus.PENDING,
        action=audit.Action.COLLECTION_SUBMITTED,
        summary="Koleksiyon onaya gönderildi",
    )


def withdraw(actor: User, collection_id: int) -> Collection:
    """Pending approval back to draft, by whoever submitted it."""
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.PENDING,),
        to=CollectionStatus.DRAFT,
        action=audit.Action.COLLECTION_WITHDRAWN,
        summary="Koleksiyon onaydan geri çekildi",
        only_submitter=True,
    )


def approve(actor: User, collection_id: int) -> Collection:
    """Pending approval to published, with whatever the Owner has edited since. For a
    published Collection, applies its pending change instead."""
    if _has_change(collection_id):
        return _approve_change(actor, collection_id)
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.PENDING,),
        to=CollectionStatus.PUBLISHED,
        action=audit.Action.COLLECTION_APPROVED,
        summary="Koleksiyon onaylandı ve yayınlandı",
    )


def reject(actor: User, collection_id: int, reason: str = "") -> Collection:
    """Pending approval back to draft; the optional reason is shown to the submitter.
    For a published Collection, drops its pending change instead."""
    reason = reason.strip()
    if len(reason) > REASON_MAX:
        _refuse("reason", f"Gerekçe en fazla {REASON_MAX} karakter olabilir.")
    if _has_change(collection_id):
        return _reject_change(actor, collection_id, reason)
    return _move(
        actor,
        collection_id,
        allowed_from=(CollectionStatus.PENDING,),
        to=CollectionStatus.DRAFT,
        action=audit.Action.COLLECTION_REJECTED,
        summary="Koleksiyon reddedildi",
        reason=reason,
    )


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
    if _has_change(collection_id):
        msg = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
        raise ConflictError(msg, code="collection_has_pending_change")
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
    if in_menus := MenuItem.objects.filter(collection=collection).count():
        msg = (
            f"Bu koleksiyona {in_menus} menü öğesi bağlı; önce menüden kaldırın. "
            "Yayından kaldırmak da bir seçenek."
        )
        raise ConflictError(msg, code="collection_in_menu")
    audit.record(
        actor,
        audit.Action.COLLECTION_DELETED,
        target=collection,
        summary=f"Koleksiyon silindi: {collection.name}",
        before=_snapshot(collection),
    )
    media._schedule_names(collection.image_file_names())  # noqa: SLF001
    collection.delete()


# --- cover image --------------------------------------------------------------------

MAX_ALT = 255


def _guard_image_edit(actor: User, collection: Collection) -> None:
    """Who may change the cover image now. The Owner may, except while a published
    Collection has a pending change. Staff may on a draft only: on a published
    Collection the image is not part of a pending change, so it is the Owner's."""
    published = collection.status == CollectionStatus.PUBLISHED
    if _is_owner(actor):
        if published and hasattr(collection, "pending_change"):
            msg = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
            raise ConflictError(msg, code="collection_has_pending_change")
        return
    if collection.status == CollectionStatus.DRAFT:
        return
    if published:
        msg = "Yayındaki koleksiyonun kapak görselini yalnızca sahip değiştirebilir."
        raise ForbiddenError(msg, code="collection_owner_only")
    _refuse_staff_edit(collection)


def _clean_alt(alt: str) -> str:
    cleaned = alt.strip()
    if len(cleaned) > MAX_ALT:
        _refuse("alt_text", f"En fazla {MAX_ALT} karakter olabilir.")
    return cleaned


def _audit_image(actor: User, collection: Collection, summary: str) -> None:
    audit.record(
        actor,
        audit.Action.COLLECTION_EDITED,
        target=collection,
        summary=f"{summary}: {collection.name}",
    )


@transaction.atomic
def upload_image(
    actor: User,
    collection_id: int,
    file: UploadedFile,
    alt_text: str = "",
) -> Collection:
    """Validate and store a cover image (JPEG, PNG or WebP), replacing any old one;
    derivatives follow in the background. The old files go after the usual grace
    period."""
    collection = _locked(collection_id)
    _guard_image_edit(actor, collection)
    alt = _clean_alt(alt_text)
    data = file.read(media.MAX_BYTES + 1)
    data, extension, width, height = media._inspect(data)  # noqa: SLF001
    old = collection.image_file_names()
    name = f"collections/{uuid.uuid4().hex}{extension}"
    default_storage.save(name, ContentFile(data))
    collection.image_original = name
    collection.image_width = width
    collection.image_height = height
    collection.image_status = CategoryProfile.ImageStatus.PROCESSING
    collection.image_derivatives = []
    collection.image_alt = alt
    collection.image_error = ""
    try:
        collection.save()
        media._schedule_names(old)  # noqa: SLF001
        _audit_image(actor, collection, "Koleksiyon kapak görseli yüklendi")
    except Exception:
        default_storage.delete(name)
        raise
    transaction.on_commit(lambda: _enqueue_image(collection_id))
    return collection


def _enqueue_image(collection_id: int) -> None:
    from store_backend.catalog.tasks import process_collection_image  # noqa: PLC0415

    process_collection_image.delay(collection_id)


@transaction.atomic
def set_image_alt(actor: User, collection_id: int, alt_text: str) -> Collection:
    collection = _locked(collection_id)
    _guard_image_edit(actor, collection)
    if not collection.image_original:
        msg = "Kapak görseli yok."
        raise ConflictError(msg, code="collection_no_image")
    collection.image_alt = _clean_alt(alt_text)
    collection.save(update_fields=["image_alt", "updated_at"])
    _audit_image(actor, collection, "Koleksiyon kapak görseli açıklaması değişti")
    return collection


@transaction.atomic
def remove_image(actor: User, collection_id: int) -> Collection:
    collection = _locked(collection_id)
    _guard_image_edit(actor, collection)
    if not collection.image_original:
        return collection
    media._schedule_names(collection.image_file_names())  # noqa: SLF001
    collection.image_original = ""
    collection.image_width = collection.image_height = 0
    collection.image_status = CategoryProfile.ImageStatus.NONE
    collection.image_derivatives = []
    collection.image_alt = ""
    collection.image_error = ""
    collection.save()
    _audit_image(actor, collection, "Koleksiyon kapak görseli kaldırıldı")
    return collection


def process_image(collection_id: int) -> None:
    """Make the derivatives of a Collection's cover image. Never raises; a failure
    marks the image failed and leaves the original in place."""
    collection = Collection.objects.filter(pk=collection_id).first()
    if collection is None or not collection.image_original:
        return
    original = collection.image_original
    written: list[str] = []
    try:
        derivatives, width, height = media.make_derivatives(
            original,
            "collections",
            written,
        )
    except Exception as exc:  # noqa: BLE001
        media.logger.exception(
            "Collection %s image could not be processed",
            collection_id,
        )
        media._schedule_names(written)  # noqa: SLF001
        Collection.objects.filter(pk=collection_id, image_original=original).update(
            image_status=CategoryProfile.ImageStatus.FAILED,
            image_error=f"{type(exc).__name__}: {exc}"[:500],
            image_derivatives=[],
        )
        return
    updated = Collection.objects.filter(
        pk=collection_id,
        image_original=original,  # not replaced or removed while we worked
    ).update(
        image_status=CategoryProfile.ImageStatus.READY,
        image_error="",
        image_derivatives=derivatives,
        image_width=width,
        image_height=height,
    )
    if not updated:
        media._schedule_names(written)  # noqa: SLF001
