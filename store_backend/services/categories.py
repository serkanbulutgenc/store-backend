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
from django.db.models import Count
from django.db.models import Max
from django.db.models import Q
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import ProductCategory

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import CategoryProfile
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import media
from store_backend.shop.models import MenuItem

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet

    from store_backend.users.models import User

MAX_SEO_TITLE = 70
MAX_SEO_DESCRIPTION = 160
MAX_DESCRIPTION = 5000
MAX_ALT = 255
MAX_HOME_ORDER = 9999

EDITABLE = frozenset(
    {
        "description",
        "seo_title",
        "seo_description",
        "is_hidden",
        "image_alt",
        "show_on_home",
        "home_order",
    },
)


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def product_counts(ids: Iterable[int] | None = None) -> dict[int, int]:
    """Products directly in each Category (their primary Category), by Category id."""
    rows = ProductCategory.objects.all()
    if ids is not None:
        rows = rows.filter(category_id__in=list(ids))
    return dict(
        rows.values_list("category_id")
        .annotate(n=Count("product_id"))
        .values_list("category_id", "n"),
    )


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
    if "home_order" in changes and not (
        0 <= int(changes["home_order"]) <= MAX_HOME_ORDER
    ):
        field = "home_order"
        msg = f"Sıra 0 ile {MAX_HOME_ORDER} arasında olmalı."
        raise _invalid(field, msg)
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


# --- move ------------------------------------------------------------------


@transaction.atomic
def move(actor: User, category: Category, parent: Category | None) -> Category:
    """Move a Category with its whole subtree under `parent` (None: the top level).

    It goes last among its new siblings. Refused under itself or one of its own
    descendants, or when the deepest descendant would end up past the depth limit.
    """
    category = Category.objects.select_for_update().get(pk=category.pk)
    if parent is not None:
        parent = Category.objects.select_for_update().get(pk=parent.pk)
        if parent.path.startswith(category.path):
            msg = "Kategori kendi altına ya da alt kategorilerinin altına taşınamaz."
            raise _invalid("parent_id", msg)  # noqa: EM101
    old_parent = category.get_parent() if category.depth > 1 else None
    if old_parent == parent:
        return category
    deepest = (
        Category.objects.filter(path__startswith=category.path).aggregate(
            deepest=Max("depth"),
        )["deepest"]
        - category.depth
    )
    if (parent.depth if parent else 0) + 1 + deepest > catalog.MAX_CATEGORY_DEPTH:
        msg = "Bu taşıma ağacı beş seviyeden daha derine çıkarır."
        raise _invalid("parent_id", msg)  # noqa: EM101
    if parent is None:
        category.move(Category.get_last_root_node(), pos="last-sibling")
    else:
        category.move(parent, pos="last-child")
    category = Category.objects.get(pk=category.pk)
    siblings = Category.objects.filter(depth=category.depth).exclude(pk=category.pk)
    if parent is not None:
        siblings = siblings.filter(path__startswith=parent.path)
    top = CategoryProfile.objects.filter(category__in=siblings).aggregate(
        top=Max("sort_order"),
    )["top"]
    CategoryProfile.objects.filter(category=category).update(sort_order=(top or 0) + 1)
    category.save()  # Oscar's public-ancestor flags and the search text follow the move
    audit.record(
        actor,
        audit.Action.CATEGORY_MOVED,
        target=category,
        summary=(
            f"Kategori taşındı: {category.name} → "
            f"{parent.name if parent else 'üst seviye'}"
        ),
        before={"parent": old_parent.name if old_parent else None},
        after={"parent": parent.name if parent else None},
    )
    return category


# --- delete ----------------------------------------------------------------


def menu_item_count(category: Category) -> int:
    """How many Menu items point at this Category (ADR 0007)."""
    return MenuItem.objects.filter(category=category).count()


def menu_item_counts() -> dict[int, int]:
    """Menu items pointing at each Category, by Category id (one query for a list)."""
    return dict(
        MenuItem.objects.filter(category__isnull=False)
        .values_list("category_id")
        .annotate(n=Count("pk"))
        .values_list("category_id", "n"),
    )


@transaction.atomic
def delete(actor: User, category: Category) -> None:
    """Delete a Category and queue its image files for removal. Audited."""
    category = Category.objects.select_for_update().get(pk=category.pk)
    children = category.numchild
    products = product_counts([category.pk]).get(category.pk, 0)
    if children or products:
        parts = []
        if children:
            parts.append(f"{children} alt kategori")
        if products:
            parts.append(f"{products} ürün")
        msg = (
            f"Bu kategoride {' ve '.join(parts)} var; önce taşıyın ya da silin. "
            "Gizlemek de bir seçenek."
        )
        raise ConflictError(msg, code="category_in_use")
    if in_menus := menu_item_count(category):
        msg = (
            f"Bu kategoriye {in_menus} menü öğesi bağlı; önce menüden kaldırın. "
            "Gizlemek de bir seçenek."
        )
        raise ConflictError(msg, code="category_in_menu")
    profile = profile_of(category)
    media._schedule_names(profile.image_file_names())  # noqa: SLF001
    audit.record(
        actor,
        audit.Action.CATEGORY_DELETED,
        target=category,
        summary=f"Kategori silindi: {category.name}",
        before={"name": category.name, "slug": category.slug, "depth": category.depth},
    )
    category.delete()


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
