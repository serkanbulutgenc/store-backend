"""Banners: promotions in the Storefront's fixed slots (phase 4, section 4).

Staff draft a Banner and the Owner approves it under the shared approval rule
(`services/approval.py`). Slot, message, link, order, dates and Category are the staged
content. The images are not: Staff change them on a draft only, the Owner any time, as
for Campaign pages. A Banner is image-led (hero, mid-page, category top) or
message-led (the announcement bar); the public endpoint shows only what is complete.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from oscar.apps.catalogue.models import Category

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Banner
from store_backend.catalog.models import CategoryProfile
from store_backend.services import approval
from store_backend.services import audit
from store_backend.services import categories
from store_backend.services import media
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from typing import NoReturn

    from django.core.files.uploadedfile import UploadedFile

    from store_backend.users.models import User

MESSAGE_MAX = 200
LINK_MAX = 500
ALT_MAX = 255
POSITION_MAX = 10_000
FIELDS = ("slot", "message", "link", "position", "starts_at", "ends_at", "category_id")
IMAGES = ("image", "mobile")  # the two image sets of a Banner
SLOT_LABELS = {
    Banner.Slot.ANNOUNCEMENT: "Duyuru çubuğu",
    Banner.Slot.HOME_HERO: "Ana sayfa vitrini",
    Banner.Slot.HOME_MID: "Ana sayfa ortası",
    Banner.Slot.CATEGORY_TOP: "Kategori sayfası üstü",
}


def _refuse(field: str, message: str) -> NoReturn:
    raise UnprocessableError(message, fields={field: [message]})


# --- the approval kind --------------------------------------------------------------


def _moment(field: str, value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            _refuse(field, "Geçerli bir tarih girin.")
    if not isinstance(value, datetime):
        _refuse(field, "Geçerli bir tarih girin.")
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def _clean_link(value: object) -> str:
    link = str(value or "").strip()
    if len(link) > LINK_MAX:
        _refuse("link", f"Bağlantı en fazla {LINK_MAX} karakter olabilir.")
    internal = link.startswith("/") and not link.startswith("//")
    if link and not internal and not link.lower().startswith("https://"):
        _refuse("link", "Bağlantı / ile ya da https:// ile başlamalı.")
    return link


def _clean_placement(values: dict[str, Any]) -> dict[str, Any]:
    """Slot, message and Category are checked together (a message belongs to the
    announcement bar, a Category to the category slot) and always returned together,
    so a change of slot leaves nothing stale behind."""
    slot = values.get("slot")
    if slot not in Banner.Slot.values:
        _refuse("slot", "Geçerli bir alan seçin.")
    message = str(values.get("message") or "").strip()
    category_id = values.get("category_id")
    if slot == Banner.Slot.ANNOUNCEMENT:
        if not message:
            _refuse("message", "Duyuru için bir mesaj yazın.")
        if len(message) > MESSAGE_MAX:
            _refuse("message", f"Mesaj en fazla {MESSAGE_MAX} karakter olabilir.")
    else:
        message = ""
    if slot != Banner.Slot.CATEGORY_TOP:
        category_id = None
    elif (
        category_id is not None and not Category.objects.filter(pk=category_id).exists()
    ):
        _refuse("category_id", "Kategori bulunamadı.")
    return {"slot": slot, "message": message, "category_id": category_id}


def _clean_position(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= POSITION_MAX
    ):
        _refuse("position", f"Sıra 0 ile {POSITION_MAX} arasında olmalı.")
    return value


def _clean(changes: dict[str, Any], item: Banner | None) -> dict[str, Any]:
    """Validate the content given; the dates come back as ISO strings so the result
    survives the pending change's JSON round trip."""
    cleaned: dict[str, Any] = {}

    def current(field: str, default: Any = "") -> Any:
        return changes.get(field, getattr(item, field) if item else default)

    placement = ("slot", "message", "category_id")
    if item is None or any(field in changes for field in placement):
        cleaned.update(
            _clean_placement({f: current(f, None) for f in placement}),
        )
    if "link" in changes or not item:
        cleaned["link"] = _clean_link(current("link"))
    if "position" in changes or not item:
        cleaned["position"] = _clean_position(current("position", 0))
    if "starts_at" in changes or "ends_at" in changes or not item:
        start = _moment("starts_at", current("starts_at", None))
        end = _moment("ends_at", current("ends_at", None))
        if start and end and end <= start:
            _refuse("ends_at", "Bitiş tarihi başlangıçtan sonra olmalı.")
        cleaned["starts_at"] = start.isoformat() if start else None
        cleaned["ends_at"] = end.isoformat() if end else None
    return cleaned


def _title(banner: Banner) -> str:
    label = SLOT_LABELS[banner.slot]
    return f"{label}: {banner.message}" if banner.message else f"{label} #{banner.pk}"


KIND = approval.register(
    approval.Kind(
        key="banner",
        label="Banner",
        model=Banner,
        manage_perm=Perm.MANAGE_BANNERS,
        edit_perm=Perm.EDIT_BANNERS,
        fields=FIELDS,
        title=_title,
        clean=_clean,
    ),
)


def _is_owner(actor: User) -> bool:
    return approval.can_manage(actor, KIND)


# --- Dashboard: reading -------------------------------------------------------------


def _get(banner_id: int) -> Banner:
    banner = (
        Banner.objects.select_related("submitted_by", "category")
        .filter(pk=banner_id)
        .first()
    )
    if banner is None:
        raise NotFoundError
    return banner


def _person(user: User | None) -> dict[str, Any] | None:
    return {"id": user.pk, "name": user.name or user.email} if user else None


def image_view(banner: Banner, which: str) -> dict[str, Any] | None:
    prefix = "image_" if which == "image" else "mobile_"
    original = getattr(banner, f"{prefix}original")
    if not original:
        return None
    return {
        "status": getattr(banner, f"{prefix}status"),
        "alt_text": banner.image_alt,
        "original_url": media.media_url(original),
        "width": getattr(banner, f"{prefix}width"),
        "height": getattr(banner, f"{prefix}height"),
        "derivatives": banner.image_derivative_urls(which),
        "error": getattr(banner, f"{prefix}error"),
    }


def _base(banner: Banner) -> dict[str, Any]:
    category = banner.category
    return {
        "id": banner.pk,
        "slot": banner.slot,
        "message": banner.message,
        "link": banner.link,
        "position": banner.position,
        "starts_at": banner.starts_at,
        "ends_at": banner.ends_at,
        "category": {"id": category.pk, "name": category.name} if category else None,
        "status": banner.status,
        "published_at": banner.published_at,
        "submitted_by": _person(banner.submitted_by),
        "submitted_at": banner.submitted_at,
        "rejection_reason": banner.rejection_reason,
        "created_at": banner.created_at,
        "updated_at": banner.updated_at,
    }


def view(banner: Banner) -> dict[str, Any]:
    change = approval.pending_change(KIND, banner.pk)
    return {
        **_base(banner),
        "image": image_view(banner, "image"),
        "mobile_image": image_view(banner, "mobile"),
        "pending_change": (
            {
                "payload": change.payload,
                "submitted_by": _person(change.submitted_by),
                "updated_at": change.updated_at,
            }
            if change
            else None
        ),
    }


def get(banner_id: int) -> dict[str, Any]:
    return view(_get(banner_id))


def listing(status: str | None = None, slot: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in ApprovalStatus.values:
        _refuse("status", "Bilinmeyen durum.")
    if slot is not None and slot not in Banner.Slot.values:
        _refuse("slot", "Bilinmeyen alan.")
    banners = Banner.objects.select_related("submitted_by", "category")
    if status:
        banners = banners.filter(status=status)
    if slot:
        banners = banners.filter(slot=slot)
    return [
        {**_base(b), "has_pending_change": approval.has_pending_change(KIND, b.pk)}
        for b in banners
    ]


# --- Dashboard: writing -------------------------------------------------------------
# The state changes (submit, withdraw, approve, reject, publish, unpublish) are the
# approval rule's own.


def create(actor: User, **values: Any) -> Banner:
    banner = approval.create(actor, KIND, **values)
    banner.refresh_from_db()
    return banner


def edit(actor: User, banner_id: int, **changes: Any) -> Banner:
    approval.edit(actor, KIND, banner_id, **changes)
    return _get(banner_id)


# --- images -------------------------------------------------------------------------


def _guard_image_edit(actor: User, banner: Banner) -> None:
    """The Owner may always change the images. Staff may on a draft only: on a pending
    or live Banner the images are not part of a staged change, so they are the
    Owner's."""
    if _is_owner(actor) or banner.status == ApprovalStatus.DRAFT:
        return
    if banner.status == ApprovalStatus.PENDING:
        msg = "Onay bekleyen banner düzenlenemez; önce geri çekin."
        raise ConflictError(msg, code="approval_state")
    msg = "Yayındaki bannerın görsellerini yalnızca sahip değiştirebilir."
    raise ForbiddenError(msg, code="approval_owner_only")


def _locked(banner_id: int) -> Banner:
    banner = Banner.objects.select_for_update().filter(pk=banner_id).first()
    if banner is None:
        raise NotFoundError
    return banner


def _clean_alt(alt: str) -> str:
    cleaned = alt.strip()
    if len(cleaned) > ALT_MAX:
        _refuse("alt_text", f"En fazla {ALT_MAX} karakter olabilir.")
    return cleaned


def _audit_image(actor: User, banner: Banner, summary: str) -> None:
    audit.record(
        actor,
        audit.Action.APPROVAL_EDITED,
        target=banner,
        summary=f"{summary}: {_title(banner)}",
    )


def _prefix(which: str) -> str:
    if which not in IMAGES:
        raise NotFoundError
    return "image_" if which == "image" else "mobile_"


@transaction.atomic
def upload_image(
    actor: User,
    banner_id: int,
    which: str,
    file: UploadedFile,
    alt_text: str | None = None,
) -> Banner:
    """Validate and store the `image` or `mobile` image (JPEG, PNG or WebP), replacing
    any old one; derivatives follow in the background. `alt_text`, when given, is the
    Banner's alt text (it covers both images)."""
    prefix = _prefix(which)
    banner = _locked(banner_id)
    _guard_image_edit(actor, banner)
    if banner.slot == Banner.Slot.ANNOUNCEMENT:
        msg = "Duyuru çubuğu görsel almaz."
        raise ConflictError(msg, code="banner_no_image_slot")
    alt = _clean_alt(alt_text) if alt_text is not None else None
    data = file.read(media.MAX_BYTES + 1)
    data, extension, width, height = media._inspect(data)  # noqa: SLF001
    old = banner.image_file_names(which)
    name = f"banners/{uuid.uuid4().hex}{extension}"
    default_storage.save(name, ContentFile(data))
    setattr(banner, f"{prefix}original", name)
    setattr(banner, f"{prefix}width", width)
    setattr(banner, f"{prefix}height", height)
    setattr(banner, f"{prefix}status", CategoryProfile.ImageStatus.PROCESSING)
    setattr(banner, f"{prefix}derivatives", [])
    setattr(banner, f"{prefix}error", "")
    if alt is not None:
        banner.image_alt = alt
    try:
        banner.save()
        media._schedule_names(old)  # noqa: SLF001
        _audit_image(actor, banner, "Banner görseli yüklendi")
    except Exception:
        default_storage.delete(name)
        raise
    transaction.on_commit(lambda: _enqueue_image(banner_id, which))
    return banner


def _enqueue_image(banner_id: int, which: str) -> None:
    from store_backend.catalog.tasks import process_banner_image  # noqa: PLC0415

    process_banner_image.delay(banner_id, which)


@transaction.atomic
def set_image_alt(actor: User, banner_id: int, alt_text: str) -> Banner:
    banner = _locked(banner_id)
    _guard_image_edit(actor, banner)
    banner.image_alt = _clean_alt(alt_text)
    banner.save(update_fields=["image_alt", "updated_at"])
    _audit_image(actor, banner, "Banner görsel açıklaması değişti")
    return banner


@transaction.atomic
def remove_image(actor: User, banner_id: int, which: str) -> Banner:
    prefix = _prefix(which)
    banner = _locked(banner_id)
    _guard_image_edit(actor, banner)
    if not getattr(banner, f"{prefix}original"):
        return banner
    media._schedule_names(banner.image_file_names(which))  # noqa: SLF001
    setattr(banner, f"{prefix}original", "")
    setattr(banner, f"{prefix}width", 0)
    setattr(banner, f"{prefix}height", 0)
    setattr(banner, f"{prefix}status", CategoryProfile.ImageStatus.NONE)
    setattr(banner, f"{prefix}derivatives", [])
    setattr(banner, f"{prefix}error", "")
    banner.save()
    _audit_image(actor, banner, "Banner görseli kaldırıldı")
    return banner


def process_image(banner_id: int, which: str) -> None:
    """Make the derivatives of one Banner image. Never raises; a failure marks the
    image failed and leaves the original in place."""
    prefix = _prefix(which)
    banner = Banner.objects.filter(pk=banner_id).first()
    original = getattr(banner, f"{prefix}original", "") if banner else ""
    if not original:
        return
    written: list[str] = []
    still_current = {"pk": banner_id, f"{prefix}original": original}
    try:
        derivatives, width, height = media.make_derivatives(
            original,
            "banners",
            written,
        )
    except Exception as exc:  # noqa: BLE001
        media.logger.exception("Banner %s image could not be processed", banner_id)
        media._schedule_names(written)  # noqa: SLF001
        Banner.objects.filter(**still_current).update(
            **{
                f"{prefix}status": CategoryProfile.ImageStatus.FAILED,
                f"{prefix}error": f"{type(exc).__name__}: {exc}"[:500],
                f"{prefix}derivatives": [],
            },
        )
        return
    updated = Banner.objects.filter(**still_current).update(
        **{
            f"{prefix}status": CategoryProfile.ImageStatus.READY,
            f"{prefix}error": "",
            f"{prefix}derivatives": derivatives,
            f"{prefix}width": width,
            f"{prefix}height": height,
        },
    )
    if not updated:
        media._schedule_names(written)  # noqa: SLF001


# --- Storefront ---------------------------------------------------------------------


def _picture(banner: Banner, which: str) -> list[dict] | None:
    """The sizes of one image once its derivatives are ready, else None."""
    prefix = _prefix(which)
    if getattr(banner, f"{prefix}status") != CategoryProfile.ImageStatus.READY:
        return None
    sizes = banner.image_derivative_urls(which)
    return sorted(sizes, key=lambda s: s["width"]) or None


def _card(banner: Banner) -> dict[str, Any] | None:
    """One Banner as the Storefront draws it, or None while it is incomplete."""
    if banner.slot == Banner.Slot.ANNOUNCEMENT:
        if not banner.message:
            return None
        return {"id": banner.pk, "message": banner.message, "link": banner.link}
    desktop = _picture(banner, "image")
    if desktop is None:
        return None
    return {
        "id": banner.pk,
        "alt": banner.image_alt,
        "link": banner.link,
        "image": desktop,
        "mobile_image": _picture(banner, "mobile"),
    }


def public(category_slug: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """The active Banners per slot: published, inside their dates, in the Owner's
    order. The category slot is filled only when a Category is given, with the Banners
    tied to it and those tied to none. An unknown or hidden Category is not found."""
    category = categories.get_visible(category_slug) if category_slug else None
    now = timezone.now()
    live = (
        Banner.objects.filter(status=ApprovalStatus.PUBLISHED)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .order_by("position", "pk")
    )
    result: dict[str, list[dict[str, Any]]] = {slot: [] for slot in Banner.Slot.values}
    for banner in live:
        if banner.slot == Banner.Slot.CATEGORY_TOP and (
            category is None or banner.category_id not in (None, category.pk)
        ):
            continue
        card = _card(banner)
        if card:
            result[banner.slot].append(card)
    return result
