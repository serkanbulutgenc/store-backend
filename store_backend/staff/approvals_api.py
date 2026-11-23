"""The Owner's approval queue: what Staff have submitted, and the decisions on it."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires_owner
from store_backend.catalog.collections_api import PersonOut  # noqa: TC001
from store_backend.catalog.collections_api import RejectIn  # noqa: TC001
from store_backend.services import approval_queue

router = Router(tags=["approvals"])


class QueueItemOut(Schema):
    type: str
    label: str
    id: int
    title: str
    is_change: bool  # an edit of a live item, not a first draft
    submitted_by: PersonOut | None
    submitted_at: datetime | None


class QueueOut(Schema):
    count: int
    items: list[QueueItemOut]


class FieldDiffOut(Schema):
    field: str
    live: Any = None
    pending: Any = None
    changed: bool


class QueueDetailOut(QueueItemOut):
    fields: list[FieldDiffOut]


class DoneOut(Schema):
    count: int  # what is still waiting


@router.get(
    "/approvals",
    auth=requires_owner(),
    response=QueueOut,
    operation_id="approvals_list",
)
def list_queue(request):
    rows = approval_queue.items()
    return {"count": len(rows), "items": rows}


@router.get(
    "/approvals/{kind}/{item_id}",
    auth=requires_owner(),
    response=QueueDetailOut,
    operation_id="approvals_get",
)
def get_item(request, kind: str, item_id: int):
    return approval_queue.get(kind, item_id)


@router.post(
    "/approvals/{kind}/{item_id}/approve",
    auth=requires_owner(),
    response=DoneOut,
    operation_id="approvals_approve",
)
def approve(request, kind: str, item_id: int):
    approval_queue.approve(request.user, kind, item_id)
    return {"count": approval_queue.count()}


@router.post(
    "/approvals/{kind}/{item_id}/reject",
    auth=requires_owner(),
    response=DoneOut,
    operation_id="approvals_reject",
)
def reject(request, kind: str, item_id: int, payload: RejectIn):
    approval_queue.reject(request.user, kind, item_id, payload.reason)
    return {"count": approval_queue.count()}


@router.post(
    "/approvals/{kind}/{item_id}/unpublish",
    auth=requires_owner(),
    response=DoneOut,
    operation_id="approvals_unpublish",
)
def unpublish(request, kind: str, item_id: int):
    approval_queue.unpublish(request.user, kind, item_id)
    return {"count": approval_queue.count()}
