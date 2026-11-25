"""The Homepage layout API: read it for editing, save the whole list. The Owner's save
is live; a Staff save waits for the Owner (the approval queue)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 (pydantic needs it)

from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires
from store_backend.catalog.store_api import HomepageOut
from store_backend.services import homepage
from store_backend.services.homepage import Section  # noqa: TC001 (pydantic needs it)
from store_backend.staff.permissions import Perm

router = Router(tags=["homepage"])


class HomepageLayoutIn(Schema):
    """The sections in page order. Hidden ones are kept, with their settings."""

    sections: list[Section]


class PendingLayoutOut(Schema):
    sections: list[Section]
    submitted_by: str | None
    submitted_at: datetime


class HomepageLayoutOut(Schema):
    """`sections` is the live layout. `pending` is the Staff change waiting for the
    Owner; `rejection_reason` says why the last one was turned down."""

    sections: list[Section]
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
