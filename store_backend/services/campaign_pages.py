"""Campaign pages: the editorial side of a Campaign (phase 4, section 3).

Staff draft a page and the Owner publishes it under the shared approval rule
(`services/approval.py`). Title, slug, text and dates are the staged content. The cover
image and the link to a discount Campaign are not: the cover changes only on a draft
(Staff) or any time (Owner), and only the Owner links or unlinks a Campaign, since that
decides what the page promises. The discount Campaign itself is never touched here.

The text is Markdown kept as written; the Storefront renderer sanitises it when it
draws the page.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Campaign
from store_backend.catalog.models import CampaignPage
from store_backend.catalog.models import CategoryProfile
from store_backend.services import approval
from store_backend.services import audit
from store_backend.services import browse
from store_backend.services import campaign_admin
from store_backend.services import media
from store_backend.services.catalog import slugify_tr
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from typing import NoReturn

    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet

    from store_backend.users.models import User

TITLE_MAX = 200
DESCRIPTION_MAX = 20_000
ALT_MAX = 255
ITEMS_MAX = 100
FIELDS = ("title", "slug", "description", "starts_at", "ends_at")


def _refuse(field: str, message: str) -> NoReturn:
    raise UnprocessableError(message, fields={field: [message]})


# --- the approval kind --------------------------------------------------------------


def _free_slug(base: str, *, ignore: int | None = None) -> str:
    taken = CampaignPage.objects.exclude(pk=ignore)
    slug, number = base, 1
    while taken.filter(slug=slug).exists():
        number += 1
        slug = f"{base}-{number}"
    return slug


def _clean_slug(slug: str, *, ignore: int | None) -> str:
    cleaned = slugify_tr(slug)
    if not cleaned:
        _refuse("slug", "Geçerli bir adres girin.")
    if CampaignPage.objects.exclude(pk=ignore).filter(slug=cleaned).exists():
        _refuse("slug", "Bu adres başka bir kampanya sayfasında kullanılıyor.")
    return cleaned


def _moment(field: str, value: object) -> datetime | None:
    """A date as given (a datetime, or the ISO string a pending change holds)."""
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


def _clean(changes: dict[str, Any], item: CampaignPage | None) -> dict[str, Any]:
    """Validate the content fields given; the dates come back as ISO strings so the
    result survives the pending change's JSON round trip."""
    cleaned: dict[str, Any] = {}
    title = changes.get("title", item.title if item else "")
    title = str(title or "").strip()
    if "title" in changes or item is None:
        if not title:
            _refuse("title", "Başlık gerekli.")
        if len(title) > TITLE_MAX:
            _refuse("title", f"Başlık en fazla {TITLE_MAX} karakter olabilir.")
        cleaned["title"] = title
    if changes.get("slug"):
        cleaned["slug"] = _clean_slug(str(changes["slug"]), ignore=item and item.pk)
    elif item is None:
        cleaned["slug"] = _free_slug(slugify_tr(title) or "kampanya")
    if "description" in changes:
        text = str(changes["description"] or "").strip()
        if len(text) > DESCRIPTION_MAX:
            _refuse(
                "description",
                f"Metin en fazla {DESCRIPTION_MAX} karakter olabilir.",
            )
        cleaned["description"] = text
    if "starts_at" in changes or "ends_at" in changes or item is None:
        start = _moment("starts_at", changes.get("starts_at", item and item.starts_at))
        end = _moment("ends_at", changes.get("ends_at", item and item.ends_at))
        if start and end and end <= start:
            _refuse("ends_at", "Bitiş tarihi başlangıçtan sonra olmalı.")
        cleaned["starts_at"] = start.isoformat() if start else None
        cleaned["ends_at"] = end.isoformat() if end else None
    return cleaned


KIND = approval.register(
    approval.Kind(
        key="campaign_page",
        label="Kampanya sayfası",
        model=CampaignPage,
        manage_perm=Perm.MANAGE_CAMPAIGN_PAGES,
        edit_perm=Perm.EDIT_CAMPAIGN_PAGES,
        fields=FIELDS,
        title=lambda page: page.title,
        clean=_clean,
    ),
)


def _is_owner(actor: User) -> bool:
    return approval.can_manage(actor, KIND)


# --- Dashboard: reading -------------------------------------------------------------


def _get(page_id: int) -> CampaignPage:
    page = (
        CampaignPage.objects.select_related("submitted_by", "campaign")
        .filter(pk=page_id)
        .first()
    )
    if page is None:
        raise NotFoundError
    return page


def _person(user: User | None) -> dict[str, Any] | None:
    return {"id": user.pk, "name": user.name or user.email} if user else None


def image_view(page: CampaignPage) -> dict[str, Any] | None:
    if not page.image_original:
        return None
    return {
        "status": page.image_status,
        "alt_text": page.image_alt,
        "original_url": media.media_url(page.image_original),
        "width": page.image_width,
        "height": page.image_height,
        "derivatives": page.image_derivative_urls(),
        "error": page.image_error,
    }


def _base(page: CampaignPage) -> dict[str, Any]:
    campaign = page.campaign
    return {
        "id": page.pk,
        "title": page.title,
        "slug": page.slug,
        "description": page.description,
        "starts_at": page.starts_at,
        "ends_at": page.ends_at,
        "status": page.status,
        "published_at": page.published_at,
        "submitted_by": _person(page.submitted_by),
        "submitted_at": page.submitted_at,
        "rejection_reason": page.rejection_reason,
        "campaign": (
            {
                "id": campaign.pk,
                "name": campaign.name,
                "status": campaign_admin.status(campaign),
            }
            if campaign
            else None
        ),
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    }


def view(page: CampaignPage) -> dict[str, Any]:
    change = approval.pending_change(KIND, page.pk)
    return {
        **_base(page),
        "image": image_view(page),
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


def get(page_id: int) -> dict[str, Any]:
    return view(_get(page_id))


def listing(status: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in ApprovalStatus.values:
        _refuse("status", "Bilinmeyen durum.")
    pages = CampaignPage.objects.select_related("submitted_by", "campaign")
    if status:
        pages = pages.filter(status=status)
    return [
        {
            **_base(page),
            "has_pending_change": approval.has_pending_change(KIND, page.pk),
        }
        for page in pages
    ]


# --- Dashboard: writing -------------------------------------------------------------
# The state changes (submit, withdraw, approve, reject, publish, unpublish) are the
# approval rule's own; these wrappers only add what is specific to a page.


def create(actor: User, **values: Any) -> CampaignPage:
    page = approval.create(actor, KIND, **values)
    page.refresh_from_db()
    return page


def edit(actor: User, page_id: int, **changes: Any) -> CampaignPage:
    approval.edit(actor, KIND, page_id, **changes)
    return _get(page_id)


@transaction.atomic
def set_campaign(actor: User, page_id: int, campaign_id: int | None) -> CampaignPage:
    """Link the page to a discount Campaign, or unlink it. Owner only."""
    if not _is_owner(actor):
        msg = "Kampanya bağlantısını yalnızca sahip değiştirebilir."
        raise ForbiddenError(msg, code="campaign_page_owner_only")
    page = CampaignPage.objects.select_for_update().filter(pk=page_id).first()
    if page is None:
        raise NotFoundError
    campaign = None
    if campaign_id is not None:
        campaign = Campaign.objects.filter(pk=campaign_id).first()
        if campaign is None:
            _refuse("campaign_id", "Kampanya bulunamadı.")
    before = page.campaign_id
    page.campaign = campaign
    page.save(update_fields=["campaign", "updated_at"])
    audit.record(
        actor,
        audit.Action.APPROVAL_EDITED,
        target=page,
        summary=f"Kampanya sayfasının kampanya bağlantısı değişti: {page.title}",
        before={"campaign_id": before},
        after={"campaign_id": campaign_id},
    )
    return _get(page_id)


# --- cover image --------------------------------------------------------------------


def _guard_image_edit(actor: User, page: CampaignPage) -> None:
    """The Owner may always change the cover. Staff may on a draft only: on a pending
    or live page the cover is not part of a staged change, so it is the Owner's."""
    if _is_owner(actor):
        return
    if page.status == ApprovalStatus.DRAFT:
        return
    if page.status == ApprovalStatus.PENDING:
        msg = "Onay bekleyen sayfa düzenlenemez; önce geri çekin."
        raise ConflictError(msg, code="approval_state")
    msg = "Yayındaki sayfanın kapak görselini yalnızca sahip değiştirebilir."
    raise ForbiddenError(msg, code="approval_owner_only")


def _locked(page_id: int) -> CampaignPage:
    page = CampaignPage.objects.select_for_update().filter(pk=page_id).first()
    if page is None:
        raise NotFoundError
    return page


def _clean_alt(alt: str) -> str:
    cleaned = alt.strip()
    if len(cleaned) > ALT_MAX:
        _refuse("alt_text", f"En fazla {ALT_MAX} karakter olabilir.")
    return cleaned


def _audit_image(actor: User, page: CampaignPage, summary: str) -> None:
    audit.record(
        actor,
        audit.Action.APPROVAL_EDITED,
        target=page,
        summary=f"{summary}: {page.title}",
    )


@transaction.atomic
def upload_image(
    actor: User,
    page_id: int,
    file: UploadedFile,
    alt_text: str = "",
) -> CampaignPage:
    """Validate and store a cover image (JPEG, PNG or WebP), replacing any old one;
    derivatives follow in the background."""
    page = _locked(page_id)
    _guard_image_edit(actor, page)
    alt = _clean_alt(alt_text)
    data = file.read(media.MAX_BYTES + 1)
    data, extension, width, height = media._inspect(data)  # noqa: SLF001
    old = page.image_file_names()
    name = f"campaign-pages/{uuid.uuid4().hex}{extension}"
    default_storage.save(name, ContentFile(data))
    page.image_original = name
    page.image_width = width
    page.image_height = height
    page.image_status = CategoryProfile.ImageStatus.PROCESSING
    page.image_derivatives = []
    page.image_alt = alt
    page.image_error = ""
    try:
        page.save()
        media._schedule_names(old)  # noqa: SLF001
        _audit_image(actor, page, "Kampanya sayfası kapak görseli yüklendi")
    except Exception:
        default_storage.delete(name)
        raise
    transaction.on_commit(lambda: _enqueue_image(page_id))
    return page


def _enqueue_image(page_id: int) -> None:
    from store_backend.catalog.tasks import process_campaign_page_image  # noqa: PLC0415

    process_campaign_page_image.delay(page_id)


@transaction.atomic
def set_image_alt(actor: User, page_id: int, alt_text: str) -> CampaignPage:
    page = _locked(page_id)
    _guard_image_edit(actor, page)
    if not page.image_original:
        msg = "Kapak görseli yok."
        raise ConflictError(msg, code="campaign_page_no_image")
    page.image_alt = _clean_alt(alt_text)
    page.save(update_fields=["image_alt", "updated_at"])
    _audit_image(actor, page, "Kampanya sayfası kapak görseli açıklaması değişti")
    return page


@transaction.atomic
def remove_image(actor: User, page_id: int) -> CampaignPage:
    page = _locked(page_id)
    _guard_image_edit(actor, page)
    if not page.image_original:
        return page
    media._schedule_names(page.image_file_names())  # noqa: SLF001
    page.image_original = ""
    page.image_width = page.image_height = 0
    page.image_status = CategoryProfile.ImageStatus.NONE
    page.image_derivatives = []
    page.image_alt = ""
    page.image_error = ""
    page.save()
    _audit_image(actor, page, "Kampanya sayfası kapak görseli kaldırıldı")
    return page


def process_image(page_id: int) -> None:
    """Make the derivatives of a page's cover image. Never raises; a failure marks the
    image failed and leaves the original in place."""
    page = CampaignPage.objects.filter(pk=page_id).first()
    if page is None or not page.image_original:
        return
    original = page.image_original
    written: list[str] = []
    try:
        derivatives, width, height = media.make_derivatives(
            original,
            "campaign-pages",
            written,
        )
    except Exception as exc:  # noqa: BLE001
        media.logger.exception("Campaign page %s image could not be processed", page_id)
        media._schedule_names(written)  # noqa: SLF001
        CampaignPage.objects.filter(pk=page_id, image_original=original).update(
            image_status=CategoryProfile.ImageStatus.FAILED,
            image_error=f"{type(exc).__name__}: {exc}"[:500],
            image_derivatives=[],
        )
        return
    updated = CampaignPage.objects.filter(
        pk=page_id,
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


# --- Storefront ---------------------------------------------------------------------


def _public() -> QuerySet[CampaignPage]:
    """Published and started. A page that has ended is still public at its URL."""
    now = timezone.now()
    return CampaignPage.objects.filter(status=ApprovalStatus.PUBLISHED).exclude(
        starts_at__gt=now,
    )


PATH_PREFIX = "/kampanyalar/"


def path(page: CampaignPage) -> str:
    """The page's URL on the Storefront."""
    return f"{PATH_PREFIX}{page.slug}"


def is_public(page: CampaignPage) -> bool:
    """Published and started; an ended page is still public at its URL."""
    return _public().filter(pk=page.pk).exists()


def _ended(page: CampaignPage, now: datetime) -> bool:
    return page.ends_at is not None and page.ends_at <= now


def cover_image(page: CampaignPage) -> dict | None:
    """The cover once its derivatives are ready, else None."""
    if page.image_status != CategoryProfile.ImageStatus.READY:
        return None
    sizes = page.image_derivative_urls()
    if not sizes:
        return None
    return {
        "alt": page.image_alt or page.title,
        "sizes": sorted(sizes, key=lambda s: s["width"]),
    }


def public_list() -> list[dict[str, Any]]:
    """Published pages that have started and not ended, newest first."""
    now = timezone.now()
    pages = _public().exclude(ends_at__lte=now).order_by("-published_at", "-pk")
    return [
        {
            "title": page.title,
            "slug": page.slug,
            "image": cover_image(page),
            "starts_at": page.starts_at,
            "ends_at": page.ends_at,
        }
        for page in pages
    ]


def _discounted_cards(campaign: Campaign) -> list[dict]:
    profiles = list(
        browse.published()
        .filter(product_id__in=campaign_admin.affected_product_ids(campaign))
        .select_related("product")
        .order_by("-product_id")[:ITEMS_MAX],
    )
    return browse._cards(profiles)  # noqa: SLF001


def public_page(slug: str) -> dict[str, Any]:
    """What a Campaign page shows. Unpublished, draft, pending and not-yet-started
    pages are not found; an ended page is shown with `ended` set. A linked page lists
    the discounted Products while its Campaign is in force, and always the Campaign's
    dates."""
    page = _public().select_related("campaign").filter(slug=slug).first()
    if page is None:
        raise NotFoundError
    now = timezone.now()
    campaign = page.campaign
    in_force = campaign is not None and campaign_admin.status(campaign) == "active"
    return {
        "title": page.title,
        "slug": page.slug,
        "description": page.description,
        "image": cover_image(page),
        "starts_at": page.starts_at,
        "ends_at": page.ends_at,
        "ended": _ended(page, now),
        "campaign": (
            {
                "starts_at": campaign.starts_at,
                "ends_at": campaign.ends_at,
                "active": in_force,
            }
            if campaign
            else None
        ),
        "items": _discounted_cards(campaign) if campaign and in_force else [],
    }
