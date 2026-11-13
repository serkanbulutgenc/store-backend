"""Category properties: copy, image, SEO, sibling order and visibility.

Oscar owns the Category tree; `CategoryProfile` holds what the store adds. A hidden
Category takes its whole subtree off the Storefront (menu, listings, sitemap, its own
URL). Products are not touched: one that also sits in a visible Category still lists
there, and a Product only in hidden Categories stays reachable by its own URL and in
search, as before.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from oscar.apps.catalogue.models import Category

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import CategoryProfile
from store_backend.services import media

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet

MAX_SEO_TITLE = 70
MAX_SEO_DESCRIPTION = 160
MAX_DESCRIPTION = 5000
MAX_ALT = 255

EDITABLE = frozenset(
    {"description", "seo_title", "seo_description", "is_hidden", "image_alt"},
)


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def profile_of(category: Category) -> CategoryProfile:
    profile, _ = CategoryProfile.objects.get_or_create(category=category)
    return profile


# --- visibility ------------------------------------------------------------


def _hidden_paths() -> list[str]:
    return list(
        CategoryProfile.objects.filter(is_hidden=True).values_list(
            "category__path",
            flat=True,
        ),
    )


def hidden_q(prefix: str = "") -> Q:
    """Matches Categories that are hidden or sit under a hidden one."""
    q = Q()
    for path in _hidden_paths():
        q |= Q(**{f"{prefix}path__startswith": path})
    return q


def visible() -> QuerySet[Category]:
    """Categories shown on the Storefront, in sibling order (sort order, then name)."""
    hidden = hidden_q()
    qs = Category.objects.all()
    if hidden:
        qs = qs.exclude(hidden)
    return qs.select_related("profile")


def sibling_key(category: Category) -> tuple[int, str]:
    return (category.profile.sort_order, category.name.casefold())


def get_visible(slug: str) -> Category:
    """A Category the Storefront may show, or NotFoundError (hidden counts as gone)."""
    category = visible().filter(slug=slug).first()
    if category is None:
        raise NotFoundError
    return category


def visible_ids_under(category: Category) -> list[int]:
    """The Category and its visible descendants (a hidden child is left out)."""
    ids = [category.pk]
    children = visible().filter(
        path__startswith=category.path,
        depth__gt=category.depth,
    )
    ids += [c.pk for c in children]
    return ids


# --- editing ---------------------------------------------------------------


@transaction.atomic
def update(category: Category, changes: dict[str, object]) -> Category:
    """Apply the given property changes (only the keys passed)."""
    unknown = set(changes) - EDITABLE
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    profile = CategoryProfile.objects.select_for_update().get(
        pk=profile_of(category).pk,
    )
    limits = {
        "description": MAX_DESCRIPTION,
        "seo_title": MAX_SEO_TITLE,
        "seo_description": MAX_SEO_DESCRIPTION,
        "image_alt": MAX_ALT,
    }
    for field, value in changes.items():
        if field in limits:
            text = str(value or "").strip()
            if len(text) > limits[field]:
                msg = f"En fazla {limits[field]} karakter olabilir."
                raise _invalid(field, msg)
            value = text  # noqa: PLW2901
        setattr(profile, field, value)
    profile.save(update_fields=list(changes))
    return category


@transaction.atomic
def reorder(parent: Category | None, ids: list[int]) -> list[Category]:
    """Save a new order for one sibling group in a single call.

    `ids` must be exactly the group's members (the roots when `parent` is None), so
    a stale screen cannot silently drop or duplicate a Category.
    """
    if parent is None:
        siblings = Category.objects.filter(depth=1)
    else:
        siblings = Category.objects.filter(
            path__startswith=parent.path,
            depth=parent.depth + 1,
        )
    by_id = {c.pk: c for c in siblings}
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        msg = "Liste güncel değil; sayfayı yenileyip tekrar deneyin."
        raise _invalid("ids", msg)  # noqa: EM101
    for position, pk in enumerate(ids, start=1):
        CategoryProfile.objects.filter(category_id=pk).update(sort_order=position)
    return [by_id[pk] for pk in ids]


# --- image -----------------------------------------------------------------


@transaction.atomic
def upload_image(category: Category, file: UploadedFile) -> CategoryProfile:
    """Validate and store a new image (replacing any old one); derivatives follow in
    the background. The old files go after the usual grace period.
    """
    data = file.read(media.MAX_BYTES + 1)
    data, extension, width, height = media._inspect(data)  # noqa: SLF001
    profile = CategoryProfile.objects.select_for_update().get(
        pk=profile_of(category).pk,
    )
    old = profile.image_file_names()
    name = f"categories/{uuid.uuid4().hex}{extension}"
    default_storage.save(name, ContentFile(data))
    profile.image_original = name
    profile.image_width = width
    profile.image_height = height
    profile.image_status = CategoryProfile.ImageStatus.PROCESSING
    profile.image_derivatives = []
    profile.image_error = ""
    try:
        profile.save()
        media._schedule_names(old)  # noqa: SLF001
    except Exception:
        default_storage.delete(name)
        raise
    transaction.on_commit(lambda: _enqueue(category.pk))
    return profile


def _enqueue(category_id: int) -> None:
    from store_backend.catalog.tasks import process_category_image  # noqa: PLC0415

    process_category_image.delay(category_id)


@transaction.atomic
def remove_image(category: Category) -> CategoryProfile:
    profile = CategoryProfile.objects.select_for_update().get(
        pk=profile_of(category).pk,
    )
    media._schedule_names(profile.image_file_names())  # noqa: SLF001
    profile.image_original = ""
    profile.image_width = profile.image_height = 0
    profile.image_status = CategoryProfile.ImageStatus.NONE
    profile.image_derivatives = []
    profile.image_alt = ""
    profile.image_error = ""
    profile.save()
    return profile


def process_image(category_id: int) -> None:
    """Make the derivatives of a Category's image. Never raises; a failure marks the
    image failed and leaves the original in place.
    """
    profile = CategoryProfile.objects.filter(category_id=category_id).first()
    if profile is None or not profile.image_original:
        return
    original = profile.image_original
    written: list[str] = []
    try:
        derivatives, width, height = media.make_derivatives(
            original,
            "categories",
            written,
        )
    except Exception as exc:  # noqa: BLE001
        media.logger.exception("Category %s image could not be processed", category_id)
        media._schedule_names(written)  # noqa: SLF001
        CategoryProfile.objects.filter(
            category_id=category_id,
            image_original=original,
        ).update(
            image_status=CategoryProfile.ImageStatus.FAILED,
            image_error=f"{type(exc).__name__}: {exc}"[:500],
            image_derivatives=[],
        )
        return
    updated = CategoryProfile.objects.filter(
        category_id=category_id,
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


def ordered(categories: Iterable[Category]) -> list[Category]:
    return sorted(categories, key=sibling_key)
