"""A Brand's optional logo: upload, replace, remove, and the derivatives job.

Stored like a Category image (an original plus WebP derivatives made in the
background) with its own limits: PNG, JPEG or WebP, 2 MB, a long edge of at least
200 px. SVG is not accepted. Replaced and removed files go after the usual grace
period.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from store_backend.catalog.models import Brand
from store_backend.catalog.models import CategoryProfile
from store_backend.services import media

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

MAX_BYTES = 2 * 1024 * 1024
MIN_LONG_EDGE = 200
Status = CategoryProfile.ImageStatus


@transaction.atomic
def upload(brand: Brand, file: UploadedFile) -> Brand:
    """Validate and store the logo, replacing any old one; derivatives follow in the
    background."""
    brand = Brand.objects.select_for_update().get(pk=brand.pk)
    data = file.read(MAX_BYTES + 1)
    data, extension, width, height = media._inspect(  # noqa: SLF001
        data,
        min_long_edge=MIN_LONG_EDGE,
        max_bytes=MAX_BYTES,
    )
    old = brand.logo_file_names()
    name = f"brands/{uuid.uuid4().hex}{extension}"
    default_storage.save(name, ContentFile(data))
    brand.logo_original = name
    brand.logo_width = width
    brand.logo_height = height
    brand.logo_status = Status.PROCESSING
    brand.logo_derivatives = []
    brand.logo_error = ""
    try:
        brand.save()
        media._schedule_names(old)  # noqa: SLF001
    except Exception:
        default_storage.delete(name)
        raise
    brand_id = brand.pk
    transaction.on_commit(lambda: _enqueue(brand_id))
    return brand


def _enqueue(brand_id: int) -> None:
    from store_backend.catalog.tasks import process_brand_logo  # noqa: PLC0415

    process_brand_logo.delay(brand_id)


@transaction.atomic
def remove(brand: Brand) -> Brand:
    brand = Brand.objects.select_for_update().get(pk=brand.pk)
    if not brand.logo_original:
        return brand
    media._schedule_names(brand.logo_file_names())  # noqa: SLF001
    brand.logo_original = ""
    brand.logo_width = brand.logo_height = 0
    brand.logo_status = Status.NONE
    brand.logo_derivatives = []
    brand.logo_error = ""
    brand.save()
    return brand


def process(brand_id: int) -> None:
    """Make the derivatives of a Brand's logo. Never raises; a failure marks the logo
    failed and leaves the original in place."""
    brand = Brand.objects.filter(pk=brand_id).first()
    if brand is None or not brand.logo_original:
        return
    original = brand.logo_original
    written: list[str] = []
    try:
        derivatives, width, height = media.make_derivatives(original, "brands", written)
    except Exception as exc:  # noqa: BLE001
        media.logger.exception("Brand %s logo could not be processed", brand_id)
        media._schedule_names(written)  # noqa: SLF001
        Brand.objects.filter(pk=brand_id, logo_original=original).update(
            logo_status=Status.FAILED,
            logo_error=f"{type(exc).__name__}: {exc}"[:500],
            logo_derivatives=[],
        )
        return
    updated = Brand.objects.filter(
        pk=brand_id,
        logo_original=original,  # not replaced or removed while we worked
    ).update(
        logo_status=Status.READY,
        logo_error="",
        logo_derivatives=derivatives,
        logo_width=width,
        logo_height=height,
    )
    if not updated:
        media._schedule_names(written)  # noqa: SLF001
