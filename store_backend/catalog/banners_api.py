"""Dashboard API for Banners (phase 4, section 4).

Staff draft and submit; the Owner publishes. Approving or rejecting a submission is
done in the Owner's queue (`/approvals`), which every approval kind shares.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from ninja import File
from ninja import Form
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile  # noqa: TC002

from store_backend.api.permissions import requires
from store_backend.catalog.collections_api import CoverImageOut  # noqa: TC001
from store_backend.catalog.collections_api import ImageAltIn  # noqa: TC001
from store_backend.catalog.collections_api import PersonOut  # noqa: TC001
from store_backend.services import approval
from store_backend.services import banners
from store_backend.staff.permissions import Perm

router = Router(tags=["banners"])


class BannerCategoryOut(Schema):
    id: int
    name: str


class BannerChangeOut(Schema):
    payload: dict[str, Any]
    submitted_by: PersonOut | None
    updated_at: datetime


class BannerRowOut(Schema):
    id: int
    slot: str
    message: str
    link: str
    position: int
    starts_at: datetime | None
    ends_at: datetime | None
    category: BannerCategoryOut | None
    status: str
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    created_at: datetime
    updated_at: datetime
    has_pending_change: bool


class BannerOut(Schema):
    id: int
    slot: str
    message: str
    link: str
    position: int
    starts_at: datetime | None
    ends_at: datetime | None
    category: BannerCategoryOut | None
    status: str
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    created_at: datetime
    updated_at: datetime
    image: CoverImageOut | None
    mobile_image: CoverImageOut | None
    pending_change: BannerChangeOut | None


class BannerIn(Schema):
    slot: str
    message: str = ""
    link: str = ""
    position: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    category_id: int | None = None


class BannerPatch(Schema):
    """What changes; anything left out stays. Staff's edit of a published Banner is
    held for the Owner. The images are separate."""

    slot: str | None = None
    message: str | None = None
    link: str | None = None
    position: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    category_id: int | None = None


class BannerImageUploadIn(Schema):
    alt_text: str | None = None


def _given(data: BannerPatch) -> dict:
    changes = data.model_dump(exclude_unset=True)
    for required in ("slot", "message", "link", "position"):
        if required in changes and changes[required] is None:
            del changes[required]  # null means "leave it"; dates and Category may clear
    return changes


@router.get(
    "/banners",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_list",
    response=list[BannerRowOut],
)
def list_banners(request, status: str | None = None, slot: str | None = None):
    """Every Banner by slot and order; `status` and `slot` narrow it."""
    return banners.listing(status, slot)


@router.post(
    "/banners",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_create",
    response={201: BannerOut},
)
def create_banner(request, data: BannerIn):
    """A new draft."""
    return 201, banners.view(banners.create(request.auth, **data.model_dump()))


@router.get(
    "/banners/{banner_id}",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_get",
    response=BannerOut,
)
def get_banner(request, banner_id: int):
    return banners.get(banner_id)


@router.patch(
    "/banners/{banner_id}",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_edit",
    response=BannerOut,
)
def edit_banner(request, banner_id: int, data: BannerPatch):
    """Edit a draft; the Owner edits any Banner. A Staff edit of a published Banner is
    saved as a pending change and the live Banner stays as it is."""
    return banners.view(banners.edit(request.auth, banner_id, **_given(data)))


@router.post(
    "/banners/{banner_id}/submit",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_submit",
    response=BannerOut,
)
def submit_banner(request, banner_id: int):
    """Draft to pending approval."""
    approval.submit(request.auth, banners.KIND, banner_id)
    return banners.get(banner_id)


@router.post(
    "/banners/{banner_id}/withdraw",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_withdraw",
    response=BannerOut,
)
def withdraw_banner(request, banner_id: int):
    """Back to draft; only the user who submitted it."""
    approval.withdraw(request.auth, banners.KIND, banner_id)
    return banners.get(banner_id)


@router.post(
    "/banners/{banner_id}/publish",
    auth=requires(Perm.MANAGE_BANNERS),
    operation_id="banners_publish",
    response=BannerOut,
)
def publish_banner(request, banner_id: int):
    """Owner only: a draft or unpublished Banner goes live."""
    approval.publish(request.auth, banners.KIND, banner_id)
    return banners.get(banner_id)


@router.post(
    "/banners/{banner_id}/unpublish",
    auth=requires(Perm.MANAGE_BANNERS),
    operation_id="banners_unpublish",
    response=BannerOut,
)
def unpublish_banner(request, banner_id: int):
    approval.unpublish(request.auth, banners.KIND, banner_id)
    return banners.get(banner_id)


@router.delete(
    "/banners/{banner_id}",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_delete",
    response={204: None},
)
def delete_banner(request, banner_id: int):
    """The Owner deletes any Banner; Staff only a draft."""
    banners.delete(request.auth, banner_id)
    return 204, None


@router.post(
    "/banners/{banner_id}/image",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_upload_image",
    response=BannerOut,
)
def upload_image(
    request,
    banner_id: int,
    file: File[UploadedFile],
    data: Form[BannerImageUploadIn],
):
    """The main image (JPEG, PNG or WebP), replacing any existing one; derivatives are
    made in the background. `alt_text`, when sent, is the Banner's alt text. Staff on a
    draft; otherwise the Owner. Not for the announcement bar."""
    banners.upload_image(request.auth, banner_id, "image", file, data.alt_text)
    return banners.get(banner_id)


@router.patch(
    "/banners/{banner_id}/image",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_edit_image",
    response=BannerOut,
)
def edit_image(request, banner_id: int, data: ImageAltIn):
    """Change the alt text, which covers both images."""
    banners.set_image_alt(request.auth, banner_id, data.alt_text)
    return banners.get(banner_id)


@router.delete(
    "/banners/{banner_id}/image",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_delete_image",
    response=BannerOut,
)
def delete_image(request, banner_id: int):
    banners.remove_image(request.auth, banner_id, "image")
    return banners.get(banner_id)


@router.post(
    "/banners/{banner_id}/mobile-image",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_upload_mobile_image",
    response=BannerOut,
)
def upload_mobile_image(request, banner_id: int, file: File[UploadedFile]):
    """The optional image for narrow screens; same formats and limits."""
    banners.upload_image(request.auth, banner_id, "mobile", file)
    return banners.get(banner_id)


@router.delete(
    "/banners/{banner_id}/mobile-image",
    auth=requires(Perm.EDIT_BANNERS),
    operation_id="banners_delete_mobile_image",
    response=BannerOut,
)
def delete_mobile_image(request, banner_id: int):
    banners.remove_image(request.auth, banner_id, "mobile")
    return banners.get(banner_id)
