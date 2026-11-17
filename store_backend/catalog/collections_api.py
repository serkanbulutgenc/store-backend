"""The Collection management API: Staff draft and submit, the Owner approves."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from ninja import File
from ninja import Form
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile  # noqa: TC002

from store_backend.api.permissions import requires
from store_backend.services import collection_admin
from store_backend.staff.permissions import Perm

router = Router(tags=["collections"])

Status = Literal["draft", "pending_approval", "published", "unpublished"]


class PersonOut(Schema):
    id: int
    name: str


class RejectIn(Schema):
    reason: str = ""


class CollectionIn(Schema):
    name: str
    slug: str | None = None
    description: str = ""
    product_ids: list[int] = []


class CollectionPatch(Schema):
    """What the Owner changes; anything left out stays as it is. `product_ids`
    replaces the whole ordered list."""

    name: str | None = None
    slug: str | None = None
    description: str | None = None
    product_ids: list[int] | None = None


class ImageDerivativeOut(Schema):
    width: int
    url: str


class CoverImageOut(Schema):
    """The cover image: `processing` until its derivatives exist."""

    status: str
    alt_text: str
    original_url: str
    width: int
    height: int
    derivatives: list[ImageDerivativeOut]
    error: str


class ImageAltIn(Schema):
    alt_text: str


class ImageUploadIn(Schema):
    alt_text: str = ""


class CollectionRowOut(Schema):
    id: int
    name: str
    slug: str
    description: str
    status: Status
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    has_pending_change: bool
    product_count: int


class CollectionProductOut(Schema):
    id: int
    title: str
    slug: str | None
    status: Literal["draft", "published", "archived"]


class PendingChangeOut(Schema):
    """A Staff edit of a published Collection, waiting for the Owner."""

    name: str
    slug: str
    description: str
    products: list[CollectionProductOut]
    submitted_by: PersonOut | None
    submitted_at: datetime
    updated_at: datetime


class CollectionOut(Schema):
    id: int
    name: str
    slug: str
    description: str
    status: Status
    published_at: datetime | None
    submitted_by: PersonOut | None
    submitted_at: datetime | None
    rejection_reason: str
    pending_change: PendingChangeOut | None
    image: CoverImageOut | None
    products: list[CollectionProductOut]
    visible_product_count: int


def _given(data: CollectionPatch) -> dict:
    changes = data.model_dump(exclude_unset=True)
    for required in ("name", "description", "product_ids"):
        if required in changes and changes[required] is None:
            del changes[required]  # null means "leave it", like leaving it out
    return changes


@router.get(
    "/collections",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_list",
    response=list[CollectionRowOut],
)
def list_collections(request, status: Status | None = None):
    return collection_admin.listing(status)


@router.post(
    "/collections",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_create",
    response={201: CollectionOut},
)
def create_collection(request, data: CollectionIn):
    made = collection_admin.create(request.auth, **data.model_dump())
    return 201, collection_admin.view(made)


@router.get(
    "/collections/{collection_id}",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_get",
    response=CollectionOut,
)
def get_collection(request, collection_id: int):
    return collection_admin.get(collection_id)


@router.patch(
    "/collections/{collection_id}",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_edit",
    response=CollectionOut,
)
def edit_collection(request, collection_id: int, data: CollectionPatch):
    edited = collection_admin.edit(request.auth, collection_id, **_given(data))
    return collection_admin.view(edited)


@router.patch(
    "/collections/{collection_id}/pending-change",
    auth=requires(Perm.MANAGE_COLLECTIONS),
    operation_id="collections_edit_pending_change",
    response=CollectionOut,
)
def edit_pending_change(request, collection_id: int, data: CollectionPatch):
    """The Owner amends a pending change before approving it."""
    changes = _given(data)
    edited = collection_admin.edit_change(request.auth, collection_id, **changes)
    return collection_admin.view(edited)


@router.post(
    "/collections/{collection_id}/submit",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_submit",
    response=CollectionOut,
)
def submit_collection(request, collection_id: int):
    return collection_admin.view(collection_admin.submit(request.auth, collection_id))


@router.post(
    "/collections/{collection_id}/withdraw",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_withdraw",
    response=CollectionOut,
)
def withdraw_collection(request, collection_id: int):
    """Back to draft; only the Staff user who submitted it."""
    return collection_admin.view(collection_admin.withdraw(request.auth, collection_id))


@router.post(
    "/collections/{collection_id}/approve",
    auth=requires(Perm.MANAGE_COLLECTIONS),
    operation_id="collections_approve",
    response=CollectionOut,
)
def approve_collection(request, collection_id: int):
    return collection_admin.view(collection_admin.approve(request.auth, collection_id))


@router.post(
    "/collections/{collection_id}/reject",
    auth=requires(Perm.MANAGE_COLLECTIONS),
    operation_id="collections_reject",
    response=CollectionOut,
)
def reject_collection(request, collection_id: int, data: RejectIn):
    return collection_admin.view(
        collection_admin.reject(request.auth, collection_id, data.reason),
    )


@router.post(
    "/collections/{collection_id}/publish",
    auth=requires(Perm.MANAGE_COLLECTIONS),
    operation_id="collections_publish",
    response=CollectionOut,
)
def publish_collection(request, collection_id: int):
    return collection_admin.view(collection_admin.publish(request.auth, collection_id))


@router.post(
    "/collections/{collection_id}/unpublish",
    auth=requires(Perm.MANAGE_COLLECTIONS),
    operation_id="collections_unpublish",
    response=CollectionOut,
)
def unpublish_collection(request, collection_id: int):
    return collection_admin.view(
        collection_admin.unpublish(request.auth, collection_id),
    )


@router.delete(
    "/collections/{collection_id}",
    auth=requires(Perm.DELETE_CATALOGUE),
    operation_id="collections_delete",
    response={204: None},
)
def delete_collection(request, collection_id: int):
    """A hard delete: Owner-only (ADR 0005). Never touches the Products."""
    collection_admin.delete(request.auth, collection_id)
    return 204, None


@router.post(
    "/collections/{collection_id}/image",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_upload_image",
    response=CollectionOut,
)
def upload_collection_image(
    request,
    collection_id: int,
    file: File[UploadedFile],
    data: Form[ImageUploadIn],
):
    """One cover image (JPEG, PNG or WebP), replacing any existing one; derivatives
    are made in the background. Staff may on a draft; on a published Collection only
    the Owner, and not while it has a pending change."""
    made = collection_admin.upload_image(
        request.auth,
        collection_id,
        file,
        data.alt_text,
    )
    return collection_admin.view(made)


@router.patch(
    "/collections/{collection_id}/image",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_edit_image",
    response=CollectionOut,
)
def edit_collection_image(request, collection_id: int, data: ImageAltIn):
    """Change the cover image's alt text."""
    edited = collection_admin.set_image_alt(request.auth, collection_id, data.alt_text)
    return collection_admin.view(edited)


@router.delete(
    "/collections/{collection_id}/image",
    auth=requires(Perm.EDIT_COLLECTIONS),
    operation_id="collections_delete_image",
    response=CollectionOut,
)
def delete_collection_image(request, collection_id: int):
    return collection_admin.view(
        collection_admin.remove_image(request.auth, collection_id),
    )
