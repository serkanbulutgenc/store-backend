"""The Owner's "waiting for approval" queue (phase 4, section 2).

One list across every content type under the approval rule: items Staff have submitted
for a first publish, and Staff edits held as a pending change against a live item.
Collections predate the shared rule and keep their own tables (ADR 0008), so they join
the queue through a small adapter and decide through `collection_admin`.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder

from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionChange
from store_backend.catalog.models import CollectionStatus
from store_backend.services import approval
from store_backend.services import collection_admin
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.models import PendingChange

COLLECTION = "collection"
COLLECTION_LABEL = "Koleksiyon"


def _person(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.pk, "name": user.name or user.email}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _item(  # noqa: PLR0913
    *,
    key: str,
    label: str,
    pk: int,
    title: str,
    is_change: bool,
    by: Any,
    at: Any,
) -> dict[str, Any]:
    return {
        "type": key,
        "label": label,
        "id": pk,
        "title": title,
        "is_change": is_change,
        "submitted_by": _person(by),
        "submitted_at": at,
    }


def _generic_items() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for kind in approval.kinds():
        for item in kind.model.objects.filter(
            status=ApprovalStatus.PENDING,
        ).select_related("submitted_by"):
            found.append(  # noqa: PERF401
                _item(
                    key=kind.key,
                    label=kind.label,
                    pk=item.pk,
                    title=kind.title(item),
                    is_change=False,
                    by=item.submitted_by,
                    at=item.submitted_at,
                ),
            )
        changes = list(
            PendingChange.objects.filter(
                content_type=ContentType.objects.get_for_model(kind.model),
            ).select_related("submitted_by"),
        )
        live = kind.model.objects.in_bulk([c.object_id for c in changes])
        for change in changes:
            target = live.get(change.object_id)
            if target is None:
                continue
            found.append(
                _item(
                    key=kind.key,
                    label=kind.label,
                    pk=target.pk,
                    title=kind.title(target),
                    is_change=True,
                    by=change.submitted_by,
                    at=change.updated_at,
                ),
            )
    return found


def _collection_items() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for collection in Collection.objects.filter(
        status=CollectionStatus.PENDING,
    ).select_related("submitted_by"):
        found.append(  # noqa: PERF401
            _item(
                key=COLLECTION,
                label=COLLECTION_LABEL,
                pk=collection.pk,
                title=collection.name,
                is_change=False,
                by=collection.submitted_by,
                at=collection.submitted_at,
            ),
        )
    for change in CollectionChange.objects.select_related(
        "collection",
        "submitted_by",
    ).filter(collection__status=CollectionStatus.PUBLISHED):
        found.append(  # noqa: PERF401
            _item(
                key=COLLECTION,
                label=COLLECTION_LABEL,
                pk=change.collection_id,
                title=change.collection.name,
                is_change=True,
                by=change.submitted_by,
                at=change.updated_at,
            ),
        )
    return found


def items() -> list[dict[str, Any]]:
    """Everything waiting, oldest first."""
    found = _generic_items() + _collection_items()
    return sorted(
        found,
        key=lambda row: (row["submitted_at"] is None, row["submitted_at"]),
    )


def count() -> int:
    return len(items())


# --- one item, with what changed ----------------------------------------------------


def _diff(live: dict[str, Any] | None, pending: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "live": None if live is None else live.get(field),
            "pending": value,
            "changed": live is None or live.get(field) != value,
        }
        for field, value in pending.items()
    ]


def get(key: str, item_id: int) -> dict[str, Any]:
    """The item's queue row plus a field-by-field difference from the live version.
    A first draft shows every field as new."""
    row = next(
        (r for r in items() if r["type"] == key and r["id"] == item_id),
        None,
    )
    if row is None:
        raise NotFoundError
    if key == COLLECTION:
        collection = Collection.objects.get(pk=item_id)
        change = CollectionChange.objects.filter(collection=collection).first()
        snap = collection_admin._snapshot(collection)  # noqa: SLF001
        snap.pop("status")
        live = snap if change else None
        pending = collection_admin._change_snapshot(change) if change else snap  # noqa: SLF001
    else:
        kind = approval.kind_for(key)
        target = kind.model.objects.get(pk=item_id)
        change = approval.pending_change(kind, item_id)
        live = approval.jsonable(kind, target) if change else None
        # jsonb does not keep key order, so follow the kind's own field order.
        stored = change.payload if change else approval.jsonable(kind, target)
        pending = {field: stored.get(field) for field in kind.fields}
    return {
        **row,
        "fields": _diff(_jsonable(live) if live else None, _jsonable(pending)),
    }


# --- deciding -----------------------------------------------------------------------


def approve(actor: Any, key: str, item_id: int) -> None:
    if key == COLLECTION:
        collection_admin.approve(actor, item_id)
        return
    approval.approve(actor, approval.kind_for(key), item_id)


def reject(actor: Any, key: str, item_id: int, reason: str = "") -> None:
    if key == COLLECTION:
        collection_admin.reject(actor, item_id, reason)
        return
    approval.reject(actor, approval.kind_for(key), item_id, reason)


def unpublish(actor: Any, key: str, item_id: int) -> None:
    if key == COLLECTION:
        collection_admin.unpublish(actor, item_id)
        return
    approval.unpublish(actor, approval.kind_for(key), item_id)
