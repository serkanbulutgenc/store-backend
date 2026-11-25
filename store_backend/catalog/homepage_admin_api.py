"""The Homepage layout API: read it for editing, save the whole list. The Owner's save
is live; a Staff save waits for the Owner (the approval queue)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 (pydantic needs it)

from ninja import File
from ninja import Form
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile  # noqa: TC002

from store_backend.api.permissions import requires
from store_backend.catalog.collections_api import CoverImageOut  # noqa: TC001
from store_backend.catalog.store_api import HomepageOut
from store_backend.services import homepage
from store_backend.services import homepage_images
from store_backend.services.homepage import Section  # noqa: TC001 (pydantic needs it)
from store_backend.staff.permissions import Perm

router = Router(tags=["homepage"])


class HomepageLayoutIn(Schema):
    """The sections in page order. Hidden ones are kept, with their settings."""

    sections: list[Section]


class PendingLayoutOut(Schema):
    sections: list[Section]
    warnings: dict[str, str] = {}
    submitted_by: str | None
    submitted_at: datetime


class HomepageLayoutOut(Schema):
    """`sections` is the live layout. `pending` is the Staff change waiting for the
    Owner; `rejection_reason` says why the last one was turned down."""

    sections: list[Section]
    #: Section id -> why the Storefront skips that visible section (nothing to show).
    warnings: dict[str, str] = {}
    pending: PendingLayoutOut | None = None
    rejection_reason: str = ""


@router.get(
    "/homepage",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_get_for_editing",
    response=HomepageLayoutOut,
)
def get_homepage(request):
    """Every section of the layout, hidden ones included, with its settings."""
    return homepage.for_editing()


@router.put(
    "/homepage",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_save",
    response=HomepageLayoutOut,
)
def save_homepage(request, data: HomepageLayoutIn):
    """Replace the whole layout atomically. The Owner's save is live at once; a Staff
    save is held as a pending change. Every step is written to the Audit log. Section
    ids must be unique and there are at most 20 sections."""
    return homepage.save(request.auth, data.model_dump()["sections"])


class LayoutVersionOut(Schema):
    version: int
    approved_at: datetime
    approved_by: str | None
    section_count: int


@router.get(
    "/homepage/versions",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_versions",
    response=list[LayoutVersionOut],
)
def homepage_versions(request):
    """Every approved version of the layout, newest first. Staff can read it."""
    return homepage.history()


@router.post(
    "/homepage/versions/{version}/restore",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_restore",
    response=HomepageLayoutOut,
)
def restore_homepage(request, version: int):
    """Owner only: put an earlier version back live. It becomes a new version and the
    history stays as it was. Refused while a Staff change is waiting."""
    return homepage.restore(request.auth, version)


@router.get(
    "/homepage/preview",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_preview",
    response=HomepageOut,
)
def preview_homepage(request):
    """The layout resolved the way the Storefront draws it, for the Dashboard's
    preview mode: the pending change if one waits for the Owner, else the live
    layout. Signed-in Dashboard users only; the public endpoint has no preview."""
    return homepage.preview()


class LayoutImageOut(Schema):
    """One image of a section: `processing` until its pictures exist."""

    id: int
    link: str
    alt_text: str
    image: CoverImageOut | None
    mobile_image: CoverImageOut | None


class LayoutImageIn(Schema):
    alt_text: str | None = None
    link: str | None = None


@router.get(
    "/homepage/images",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_images",
    response=list[LayoutImageOut],
)
def list_images(request, ids: Query[list[int]]):
    """The section images among `ids` (at most 50), in the order given."""
    return homepage_images.listing(ids)


@router.post(
    "/homepage/images",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_upload_image",
    response={201: LayoutImageOut},
)
def upload_image(request, file: File[UploadedFile], data: Form[LayoutImageIn]):
    """A new section image (JPEG, PNG, WebP or GIF). It shows on the Storefront only
    once an approved layout uses it; Staff can upload too."""
    return 201, homepage_images.upload(
        request.auth,
        file,
        alt_text=data.alt_text,
        link=data.link,
    )


@router.post(
    "/homepage/images/{image_id}/image",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_replace_image",
    response=LayoutImageOut,
)
def replace_image(
    request,
    image_id: int,
    file: File[UploadedFile],
    data: Form[LayoutImageIn],
):
    """Replace the main picture of a section image."""
    return homepage_images.upload(
        request.auth,
        file,
        image_id=image_id,
        alt_text=data.alt_text,
        link=data.link,
    )


@router.post(
    "/homepage/images/{image_id}/mobile-image",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_upload_mobile_image",
    response=LayoutImageOut,
)
def upload_mobile_image(request, image_id: int, file: File[UploadedFile]):
    """The picture phones get instead of the main one."""
    return homepage_images.upload(request.auth, file, image_id=image_id, which="mobile")


@router.delete(
    "/homepage/images/{image_id}/mobile-image",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_delete_mobile_image",
    response=LayoutImageOut,
)
def delete_mobile_image(request, image_id: int):
    return homepage_images.remove_mobile(request.auth, image_id)


@router.patch(
    "/homepage/images/{image_id}",
    auth=requires(Perm.EDIT_HOMEPAGE),
    operation_id="homepage_edit_image",
    response=LayoutImageOut,
)
def edit_image(request, image_id: int, data: LayoutImageIn):
    """Change the alt text (it covers both pictures) or the link."""
    return homepage_images.update(
        request.auth,
        image_id,
        alt_text=data.alt_text,
        link=data.link,
    )
