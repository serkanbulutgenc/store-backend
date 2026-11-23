"""Dashboard API for Campaign pages (phase 4, section 3).

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
from store_backend.catalog.collections_api import ImageUploadIn  # noqa: TC001
from store_backend.catalog.collections_api import PersonOut  # noqa: TC001
from store_backend.services import approval
from store_backend.services import campaign_pages
from store_backend.staff.permissions import Perm

router = Router(tags=["campaign-pages"])


class CampaignPageLinkOut(Schema):
    id: int
    name: str
    status: str  # inactive, scheduled, ended or active


class CampaignPageChangeOut(Schema):
    payload: dict[str, Any]
    submitted_by: PersonOut | None
    updated_at: datetime


class CampaignPageRowOut(Schema):
    id: int
    title: str
    slug: str
    description: str
    starts_at: datetime | None
    ends_at: datetime | None
    status: str
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    campaign: CampaignPageLinkOut | None
    created_at: datetime
    updated_at: datetime
    has_pending_change: bool


class CampaignPageOut(Schema):
    id: int
    title: str
    slug: str
    description: str
    starts_at: datetime | None
    ends_at: datetime | None
    status: str
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    campaign: CampaignPageLinkOut | None
    created_at: datetime
    updated_at: datetime
    image: CoverImageOut | None
    pending_change: CampaignPageChangeOut | None


class CampaignPageIn(Schema):
    title: str
    slug: str | None = None
    description: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class CampaignPagePatch(Schema):
    """What changes; anything left out stays. Staff's edit of a published page is held
    for the Owner. The discount Campaign link and the cover image are separate."""

    title: str | None = None
    slug: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class CampaignLinkIn(Schema):
    campaign_id: int | None = None  # null unlinks


def _given(data: CampaignPagePatch) -> dict:
    changes = data.model_dump(exclude_unset=True)
    for required in ("title", "slug", "description"):
        if required in changes and changes[required] is None:
            del changes[required]  # null means "leave it"; dates may be cleared
    return changes


def _out(page) -> dict:
    return campaign_pages.view(page)


@router.get(
    "/campaign-pages",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_list",
    response=list[CampaignPageRowOut],
)
def list_pages(request, status: str | None = None):
    """Every page, newest first; `status` narrows to one state."""
    return campaign_pages.listing(status)


@router.post(
    "/campaign-pages",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_create",
    response={201: CampaignPageOut},
)
def create_page(request, data: CampaignPageIn):
    """A new draft. Without `slug` it is made from the title."""
    return 201, _out(campaign_pages.create(request.auth, **data.model_dump()))


@router.get(
    "/campaign-pages/{page_id}",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_get",
    response=CampaignPageOut,
)
def get_page(request, page_id: int):
    return campaign_pages.get(page_id)


@router.patch(
    "/campaign-pages/{page_id}",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_edit",
    response=CampaignPageOut,
)
def edit_page(request, page_id: int, data: CampaignPagePatch):
    """Edit a draft; the Owner edits any page. A Staff edit of a published page is
    saved as a pending change and the live page stays as it is."""
    return _out(campaign_pages.edit(request.auth, page_id, **_given(data)))


@router.post(
    "/campaign-pages/{page_id}/submit",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_submit",
    response=CampaignPageOut,
)
def submit_page(request, page_id: int):
    """Draft to pending approval."""
    approval.submit(request.auth, campaign_pages.KIND, page_id)
    return campaign_pages.get(page_id)


@router.post(
    "/campaign-pages/{page_id}/withdraw",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_withdraw",
    response=CampaignPageOut,
)
def withdraw_page(request, page_id: int):
    """Back to draft; only the user who submitted it."""
    approval.withdraw(request.auth, campaign_pages.KIND, page_id)
    return campaign_pages.get(page_id)


@router.post(
    "/campaign-pages/{page_id}/publish",
    auth=requires(Perm.MANAGE_CAMPAIGN_PAGES),
    operation_id="campaign_pages_publish",
    response=CampaignPageOut,
)
def publish_page(request, page_id: int):
    """Owner only: a draft or unpublished page goes live."""
    approval.publish(request.auth, campaign_pages.KIND, page_id)
    return campaign_pages.get(page_id)


@router.post(
    "/campaign-pages/{page_id}/unpublish",
    auth=requires(Perm.MANAGE_CAMPAIGN_PAGES),
    operation_id="campaign_pages_unpublish",
    response=CampaignPageOut,
)
def unpublish_page(request, page_id: int):
    approval.unpublish(request.auth, campaign_pages.KIND, page_id)
    return campaign_pages.get(page_id)


@router.put(
    "/campaign-pages/{page_id}/campaign",
    auth=requires(Perm.MANAGE_CAMPAIGN_PAGES),
    operation_id="campaign_pages_link_campaign",
    response=CampaignPageOut,
)
def link_campaign(request, page_id: int, data: CampaignLinkIn):
    """Owner only: link the page to a discount Campaign, or unlink with null. The
    Campaign itself is not changed."""
    campaign_pages.set_campaign(request.auth, page_id, data.campaign_id)
    return campaign_pages.get(page_id)


@router.post(
    "/campaign-pages/{page_id}/image",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_upload_image",
    response=CampaignPageOut,
)
def upload_image(
    request,
    page_id: int,
    file: File[UploadedFile],
    data: Form[ImageUploadIn],
):
    """One cover image (JPEG, PNG or WebP), replacing any existing one; derivatives
    are made in the background. Staff on a draft; otherwise the Owner."""
    campaign_pages.upload_image(request.auth, page_id, file, data.alt_text)
    return campaign_pages.get(page_id)


@router.patch(
    "/campaign-pages/{page_id}/image",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_edit_image",
    response=CampaignPageOut,
)
def edit_image(request, page_id: int, data: ImageAltIn):
    """Change the cover image's alt text."""
    campaign_pages.set_image_alt(request.auth, page_id, data.alt_text)
    return campaign_pages.get(page_id)


@router.delete(
    "/campaign-pages/{page_id}/image",
    auth=requires(Perm.EDIT_CAMPAIGN_PAGES),
    operation_id="campaign_pages_delete_image",
    response=CampaignPageOut,
)
def delete_image(request, page_id: int):
    campaign_pages.remove_image(request.auth, page_id)
    return campaign_pages.get(page_id)
