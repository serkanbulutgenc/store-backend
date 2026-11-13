"""Product photos: upload rules, WebP derivatives, delivery URLs and delayed removal.

The original upload is kept as it came. A background job (`process`) makes content-
hashed WebP derivatives with EXIF stripped and orientation applied; if that fails the
image is marked failed and the original stays usable. All URLs go through
`media_url()` so a later move to object storage touches one function.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Case
from django.db.models import IntegerField
from django.db.models import Max
from django.db.models import Q
from django.db.models import When
from django.utils import timezone
from oscar.apps.catalogue.models import Product
from PIL import Image
from PIL import ImageOps
from PIL import UnidentifiedImageError

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import Colour
from store_backend.catalog.models import PendingFileRemoval
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from store_backend.users.models import User

logger = logging.getLogger(__name__)

MAX_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 12
MIN_LONG_EDGE = 800
WIDTHS = (400, 800, 1600)
WEBP_QUALITY = 82
MAX_ALT_LENGTH = 255

_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_ROTATED_ORIENTATIONS = {5, 6, 7, 8}
_ORIENTATION_TAG = 0x0112
_GPS_TAG = 0x8825

MSG_FORMAT = "Yalnızca JPEG, PNG veya WebP dosyaları yüklenebilir."
MSG_HEIC = (
    "HEIC biçimi desteklenmiyor. Fotoğrafı JPEG olarak kaydedip yeniden yükleyin "
    "(iPhone: Ayarlar > Kamera > Biçimler > En Uyumlu)."
)
MSG_CORRUPT = "Fotoğraf okunamadı; dosya bozuk olabilir. Yeniden çekip yükleyin."
MSG_SIZE = "Dosya en fazla 15 MB olabilir."
MSG_SMALL = f"Görselin uzun kenarı en az {MIN_LONG_EDGE} piksel olmalı."
MSG_LIMIT = f"Bir ürüne en fazla {MAX_IMAGES} fotoğraf eklenebilir."


def _register_heif() -> bool:
    """Teach Pillow to read HEIC if `pillow-heif` is installed (optional)."""
    try:
        from pillow_heif import register_heif_opener  # noqa: PLC0415
    except ImportError:
        return False
    register_heif_opener()
    _FORMATS["HEIF"] = ".heic"
    return True


_HEIC = _register_heif()


def heic_supported() -> bool:
    return _HEIC


def media_url(name: str) -> str:
    """The public URL of a stored media file. The only place URLs are built."""
    return default_storage.url(name)


def _invalid(message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={"file": [message]})


def _looks_like_heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS


def _without_gps(img: Image.Image, fmt: str, data: bytes) -> tuple[bytes, str]:
    """Return the file to keep and its extension, with location data removed.

    A file without GPS is kept byte for byte. One with GPS is re-encoded without it
    (JPEG keeps its quantisation tables, so no further quality loss); everything else
    in the metadata, such as orientation, stays. HEIC is written back as JPEG.
    """
    exif = img.getexif()
    if not exif.get_ifd(_GPS_TAG):
        return data, _FORMATS[fmt]
    del exif[_GPS_TAG]
    exif._ifds.pop(_GPS_TAG, None)  # noqa: SLF001 - Pillow keeps a cached copy
    options: dict = {"exif": exif}
    if icc := img.info.get("icc_profile"):
        options["icc_profile"] = icc
    out_fmt = "JPEG" if fmt == "HEIF" else fmt
    if fmt == "JPEG":
        options.update(quality="keep", subsampling="keep")
    elif out_fmt in ("JPEG", "WEBP"):
        options["quality"] = 95
    buf = io.BytesIO()
    img.save(buf, out_fmt, **options)
    return buf.getvalue(), _FORMATS[out_fmt]


def _inspect(data: bytes) -> tuple[bytes, str, int, int]:
    """Validate an upload; return (bytes to store, extension, width, height) with
    the size taken after orientation.
    """
    if len(data) > MAX_BYTES:
        raise _invalid(MSG_SIZE)
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        width, height = img.size
        orientation = img.getexif().get(_ORIENTATION_TAG)
        img.load()  # decode fully: catches truncated or corrupt files
    except UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError:
        if not _looks_like_heic(data):
            raise _invalid(MSG_FORMAT) from None
        raise _invalid(MSG_CORRUPT if _HEIC else MSG_HEIC) from None
    if fmt not in _FORMATS:
        raise _invalid(MSG_FORMAT)
    if max(width, height) < MIN_LONG_EDGE:
        raise _invalid(MSG_SMALL)
    if orientation in _ROTATED_ORIENTATIONS:
        width, height = height, width
    data, extension = _without_gps(img, fmt, data)
    return data, extension, width, height


# --- upload -------------------------------------------------------------------


@transaction.atomic
def upload(
    actor: User,
    product: Product,
    file: UploadedFile,
    *,
    colour: Colour | None = None,
) -> ProductPhoto:
    """Validate and store the original, then queue the derivative job."""
    data = file.read(MAX_BYTES + 1)
    data, extension, width, height = _inspect(data)
    Product.objects.select_for_update().get(pk=product.pk)  # serialise uploads
    existing = ProductPhoto.objects.filter(product=product)
    if existing.count() >= MAX_IMAGES:
        raise _invalid(MSG_LIMIT)
    top = existing.aggregate(top=Max("sort_order"))["top"] or 0
    name = f"products/{uuid.uuid4().hex}{extension}"
    image = ProductPhoto.objects.create(
        product=product,
        colour=colour,
        sort_order=top + 1,
        is_primary=not existing.exists(),
        original=name,
        width=width,
        height=height,
    )
    default_storage.save(name, ContentFile(data))
    try:
        audit.record(
            actor,
            audit.Action.PHOTO_UPLOAD,
            target=image,
            summary=f"{product.title}: fotoğraf yüklendi",
            after={"product_id": product.pk, "image_id": image.pk},
        )
    except Exception:
        default_storage.delete(name)  # the row rolls back, so the file must not stay
        raise
    transaction.on_commit(lambda: _enqueue(image.pk))
    return image


def _enqueue(image_id: int) -> None:
    from store_backend.catalog.tasks import process_product_image  # noqa: PLC0415

    process_product_image.delay(image_id)


# --- processing -------------------------------------------------------------


def _clean(img: Image.Image) -> Image.Image:
    """Orientation applied, colour mode WebP can store, no metadata."""
    img = ImageOps.exif_transpose(img)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.info = {}
    return img


def make_derivatives(
    original: str,
    folder: str,
    written: list[str],
) -> tuple[list[dict], int, int]:
    """Write the WebP derivatives of a stored original; returns (derivatives, width,
    height). New file names are appended to `written` as they are saved, so a caller
    can clean up after a failure part-way.
    """
    with default_storage.open(original) as f:
        opened = Image.open(f)
        opened.load()
    img = _clean(opened)
    widths = [w for w in WIDTHS if w <= img.width] or [img.width]
    derivatives = []
    for width in widths:
        height = max(1, round(img.height * width / img.width))
        resized = img if width == img.width else img.resize((width, height))
        buf = io.BytesIO()
        resized.save(buf, "WEBP", quality=WEBP_QUALITY)
        data = buf.getvalue()
        name = f"{folder}/{hashlib.sha256(data).hexdigest()[:16]}-{width}.webp"
        if not default_storage.exists(name):
            default_storage.save(name, ContentFile(data))
            written.append(name)
        derivatives.append({"width": width, "name": name})
    return derivatives, img.width, img.height


def process(image_id: int) -> None:
    """Make the WebP derivatives of one image. Never raises: a failure marks the
    image failed and leaves the original in place.
    """
    image = ProductPhoto.objects.filter(pk=image_id).first()
    if image is None:
        return
    written: list[str] = []
    try:
        derivatives, width, height = make_derivatives(
            image.original,
            "products",
            written,
        )
    except Exception as exc:
        logger.exception("Image %s could not be processed", image_id)
        _schedule_names(written)
        ProductPhoto.objects.filter(pk=image_id).update(
            status=ProductPhoto.Status.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:500],
            derivatives=[],
        )
        return
    updated = ProductPhoto.objects.filter(pk=image_id).update(
        status=ProductPhoto.Status.READY,
        error="",
        derivatives=derivatives,
        width=width,
        height=height,
    )
    if not updated:  # deleted while we worked: its delete did not know these files
        _schedule_names(written)


# --- editing ------------------------------------------------------------------

_UNSET = object()


def _editable(image: ProductPhoto) -> dict:
    return {
        "colour_id": image.colour_id,
        "alt_text": image.alt_text,
        "is_primary": image.is_primary,
    }


@transaction.atomic
def update(
    actor: User,
    image: ProductPhoto,
    *,
    colour: Colour | object | None = _UNSET,
    alt_text: str | object = _UNSET,
    is_primary: bool | object = _UNSET,
) -> ProductPhoto:
    before = _editable(image)
    if colour is not _UNSET:
        image.colour = colour  # type: ignore[assignment]
    if alt_text is not _UNSET:
        alt = str(alt_text).strip()
        if len(alt) > MAX_ALT_LENGTH:
            msg = f"Alternatif metin en fazla {MAX_ALT_LENGTH} karakter olabilir."
            raise UnprocessableError(msg, fields={"alt_text": [msg]})
        image.alt_text = alt
    if is_primary is True and not image.is_primary:
        Product.objects.select_for_update().get(pk=image.product_id)
        ProductPhoto.objects.filter(product_id=image.product_id).update(
            is_primary=False,
        )
        image.is_primary = True
    elif is_primary is False and image.is_primary:
        msg = "Başka bir fotoğrafı ana fotoğraf yapın."
        raise UnprocessableError(msg, fields={"is_primary": [msg]})
    image.save()
    audit.record(
        actor,
        audit.Action.PHOTO_EDIT,
        target=image,
        summary=f"{image.product.title}: fotoğraf düzenlendi",
        before=before,
        after=_editable(image),
    )
    return image


@transaction.atomic
def reorder(actor: User, product: Product, ids: list[int]) -> list[ProductPhoto]:
    """Set the display order; `ids` must be exactly the Product's images."""
    images = {i.pk: i for i in ProductPhoto.objects.filter(product=product)}
    if len(ids) != len(set(ids)) or set(ids) != set(images):
        msg = "Sıralama tüm fotoğrafları bir kez içermelidir."
        raise UnprocessableError(msg, fields={"ids": [msg]})
    ordered = [images[pk] for pk in ids]
    for position, image in enumerate(ordered, start=1):
        image.sort_order = position
    ProductPhoto.objects.bulk_update(ordered, ["sort_order"])
    audit.record(
        actor,
        audit.Action.PHOTO_EDIT,
        target=product,
        summary=f"{product.title}: fotoğraf sırası değişti",
        after={"order": ids},
    )
    return ordered


# --- delete and delayed removal ---------------------------------------------


def _ready_images(product_id: int):
    return ProductPhoto.objects.filter(
        product_id=product_id,
        status=ProductPhoto.Status.READY,
    )


@transaction.atomic
def delete(actor: User, image: ProductPhoto) -> None:
    """Delete the record now; its files go after the grace period (see signals)."""
    product = Product.objects.select_for_update().get(pk=image.product_id)
    # Read after the lock: a publish that just committed must be seen.
    published = (
        ProductProfile.objects.get(product=product).status == ProductStatus.PUBLISHED
    )
    others = _ready_images(product.pk).exclude(pk=image.pk).exists()
    if published and image.status == ProductPhoto.Status.READY and not others:
        msg = "Yayındaki ürünün son fotoğrafı silinemez."
        raise ConflictError(msg, code="last_image")
    audit.record(
        actor,
        audit.Action.PHOTO_DELETE,
        target=image,
        summary=f"{product.title}: fotoğraf silindi",
        before={"product_id": product.pk, "image_id": image.pk},
    )
    was_primary = image.is_primary
    image.delete()
    if was_primary:
        successor = (
            ProductPhoto.objects.filter(product=product)
            .annotate(
                usable=Case(
                    When(status=ProductPhoto.Status.READY, then=0),
                    default=1,
                    output_field=IntegerField(),
                ),
            )
            .order_by("usable", "sort_order", "pk")
            .first()
        )
        if successor is not None:
            ProductPhoto.objects.filter(pk=successor.pk).update(is_primary=True)


def _schedule_names(names: list[str]) -> None:
    due = timezone.now() + timedelta(days=settings.MEDIA_REMOVAL_GRACE_DAYS)
    PendingFileRemoval.objects.bulk_create(
        PendingFileRemoval(name=name, remove_after=due) for name in names
    )


def schedule_removal(image: ProductPhoto) -> None:
    """Queue an image's files for deletion after the grace period."""
    _schedule_names(image.file_names())


def _in_use(name: str) -> bool:
    return (
        ProductPhoto.objects.filter(
            Q(original=name) | Q(derivatives__contains=[{"name": name}]),
        ).exists()
        or CategoryProfile.objects.filter(
            Q(image_original=name) | Q(image_derivatives__contains=[{"name": name}]),
        ).exists()
    )


def purge_removed_files() -> int:
    """Delete files whose grace period is over, unless an image still uses them
    (identical photos share content-hashed derivative names). Returns rows handled.
    """
    due = list(PendingFileRemoval.objects.filter(remove_after__lte=timezone.now()))
    for pending in due:
        if not _in_use(pending.name):
            default_storage.delete(pending.name)
    PendingFileRemoval.objects.filter(pk__in=[p.pk for p in due]).delete()
    return len(due)


# --- publish rule -----------------------------------------------------------


def require_ready_image(product: Product) -> None:
    if _ready_images(product.pk).exists():
        return
    msg = "Yayınlamak için en az bir fotoğraf ekleyin."
    if ProductPhoto.objects.filter(product=product).exists():
        msg = "Fotoğraflar işleniyor veya işlenemedi; yayınlamadan önce kontrol edin."
    raise UnprocessableError(msg, fields={"status": [msg]})


if require_ready_image not in catalog.PUBLISH_CHECKS:
    catalog.PUBLISH_CHECKS.append(require_ready_image)
