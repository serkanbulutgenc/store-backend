"""The approval rule shared by content Staff draft and the Owner publishes (phase 4,
section 2; ADR 0008 for the pending-change idea).

A content type registers a `Kind`: its model (which mixes in `ApprovalFields`), the
fields that make up its content, and a `clean` that validates a set of changes. The
rules here then apply to every kind alike:

- Staff create and edit drafts and submit them; only the Owner approves, rejects,
  publishes or unpublishes.
- A Staff edit to a published item is held as one pending change. The live item is
  untouched until the Owner approves it; a second Staff edit amends it. While a pending
  change exists the Owner cannot edit or unpublish the live item, so neither side's
  work is lost.
- Every published version is kept (`ApprovedVersion`), and every step is audited.

Payload values must survive a JSON round trip (`clean` turns dates into ISO strings).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.models import ApprovedVersion
from store_backend.staff.models import PendingChange

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import Model

    from store_backend.staff.permissions import Perm

REASON_MAX = 500


@dataclass(frozen=True)
class Kind:
    key: str  # in URLs and the queue, e.g. "campaign_page"
    label: str  # Turkish name shown to people, e.g. "Kampanya sayfası"
    model: type[Model]
    manage_perm: Perm  # approve, reject, publish, unpublish: held by the Owner
    edit_perm: Perm  # draft, edit, submit, withdraw: held by Staff too
    fields: tuple[str, ...]  # the content that is staged and versioned
    title: Callable[[Any], str]
    # (changes, item or None) -> cleaned changes, raising UnprocessableError.
    clean: Callable[[dict[str, Any], Any], dict[str, Any]]


_KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    _KINDS[kind.key] = kind
    return kind


def kinds() -> list[Kind]:
    return list(_KINDS.values())


def kind_for(key: str) -> Kind:
    found = _KINDS.get(key)
    if found is None:
        raise NotFoundError
    return found


# --- helpers ------------------------------------------------------------------------


def can_manage(actor: Any, kind: Kind) -> bool:
    """Whether `actor` may approve and publish this kind (the Owner)."""
    return actor.has_perm(kind.manage_perm.permission_name)


def _require_manage(actor: Any, kind: Kind) -> None:
    if not can_manage(actor, kind):
        raise ForbiddenError


def _require_edit(actor: Any, kind: Kind) -> None:
    if not (can_manage(actor, kind) or actor.has_perm(kind.edit_perm.permission_name)):
        raise ForbiddenError


def _refuse(field: str, message: str) -> None:
    raise UnprocessableError(message, fields={field: [message]})


def _ct(kind: Kind) -> ContentType:
    return ContentType.objects.get_for_model(kind.model)


def snapshot(kind: Kind, item: Any) -> dict[str, Any]:
    return {field: getattr(item, field) for field in kind.fields}


def jsonable(kind: Kind, item: Any) -> dict[str, Any]:
    """The snapshot as stored: dates as strings, so it equals what a payload reads
    back as."""
    return json.loads(json.dumps(snapshot(kind, item), cls=DjangoJSONEncoder))


def _locked(kind: Kind, item_id: int) -> Any:
    item = kind.model.objects.select_for_update().filter(pk=item_id).first()
    if item is None:
        raise NotFoundError
    return item


def pending_change(kind: Kind, item_id: int) -> PendingChange | None:
    return PendingChange.objects.filter(
        content_type=_ct(kind),
        object_id=item_id,
    ).first()


def has_pending_change(kind: Kind, item_id: int) -> bool:
    return pending_change(kind, item_id) is not None


def _record(  # noqa: PLR0913
    actor: Any,
    action: audit.Action,
    kind: Kind,
    item: Any,
    summary: str,
    *,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    audit.record(
        actor,
        action,
        target=item,
        summary=f"{kind.label} {summary}: {kind.title(item)}",
        before=before,
        after=after,
    )


def _save_version(kind: Kind, item: Any, actor: Any) -> ApprovedVersion:
    ct = _ct(kind)
    last = ApprovedVersion.objects.filter(content_type=ct, object_id=item.pk).aggregate(
        top=Max("version"),
    )["top"]
    return ApprovedVersion.objects.create(
        content_type=ct,
        object_id=item.pk,
        version=(last or 0) + 1,
        payload=jsonable(kind, item),
        approved_by=actor,
    )


def keep_version(kind: Kind, item: Any, actor: Any) -> ApprovedVersion:
    """Keep the item's current content as its next version, for a save that is live
    without going through approve or publish (the Owner's own save)."""
    return _save_version(kind, item, actor)


def versions(kind: Kind, item_id: int) -> list[ApprovedVersion]:
    """Every approved version of the item, newest first."""
    return list(
        ApprovedVersion.objects.filter(content_type=_ct(kind), object_id=item_id),
    )


# --- editing ------------------------------------------------------------------------


def create(actor: Any, kind: Kind, **values: Any) -> Any:
    """A new draft. Staff and the Owner may both start one."""
    _require_edit(actor, kind)
    cleaned = kind.clean(values, None)
    item = kind.model(**cleaned)
    item.save()
    _record(actor, audit.Action.APPROVAL_EDITED, kind, item, "oluşturuldu")
    return item


@transaction.atomic
def edit(actor: Any, kind: Kind, item_id: int, **changes: Any) -> Any:
    """Change the fields given; leave the rest.

    The Owner edits any item directly, except a published one with a pending change
    (approve or reject that first). Staff edit drafts directly; their edit of a
    published item becomes, or amends, its pending change."""
    _require_edit(actor, kind)
    item = _locked(kind, item_id)
    live = item.status == ApprovalStatus.PUBLISHED
    owner = can_manage(actor, kind)
    if owner:
        if live and has_pending_change(kind, item.pk):
            msg = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
            raise ConflictError(msg, code="approval_has_pending_change")
    elif live:
        _stage(actor, kind, item, changes)
        return item
    elif item.status == ApprovalStatus.PENDING:
        msg = "Onay bekleyen kayıt düzenlenemez; önce geri çekin."
        raise ConflictError(msg, code="approval_state")
    elif item.status != ApprovalStatus.DRAFT:
        msg = "Yayından kaldırılan kaydı yalnızca sahip düzenleyebilir."
        raise ForbiddenError(msg, code="approval_owner_only")
    before = jsonable(kind, item)
    for field, value in kind.clean(changes, item).items():
        setattr(item, field, value)
    item.save()
    _record(
        actor,
        audit.Action.APPROVAL_EDITED,
        kind,
        item,
        "düzenlendi",
        before=before,
        after=jsonable(kind, item),
    )
    return item


def _stage(actor: Any, kind: Kind, item: Any, changes: dict[str, Any]) -> None:
    """Hold a Staff edit of a published item as its one pending change, or amend it.
    The live item is not touched."""
    current = pending_change(kind, item.pk)
    base = dict(current.payload) if current else jsonable(kind, item)
    cleaned = kind.clean({**base, **changes}, item)
    payload = {field: cleaned[field] for field in kind.fields if field in cleaned}
    before = dict(current.payload) if current else None
    if current is None:
        current = PendingChange(content_type=_ct(kind), object_id=item.pk)
    current.payload = {**base, **payload}
    current.submitted_by = actor
    current.save()
    item.rejection_reason = ""
    item.save(update_fields=["rejection_reason"])
    _record(
        actor,
        audit.Action.APPROVAL_CHANGE_SUBMITTED,
        kind,
        item,
        "değişikliği " + ("güncellendi" if before else "onaya gönderildi"),
        before=before,
        after=current.payload,
    )


@transaction.atomic
def edit_change(actor: Any, kind: Kind, item_id: int, **changes: Any) -> Any:
    """The Owner amends a pending change before approving it."""
    _require_manage(actor, kind)
    item = _locked(kind, item_id)
    current = pending_change(kind, item.pk)
    if current is None:
        msg = "Bekleyen bir değişiklik yok."
        raise ConflictError(msg, code="approval_no_pending_change")
    before = dict(current.payload)
    cleaned = kind.clean({**before, **changes}, item)
    current.payload = {field: cleaned[field] for field in kind.fields}
    current.save()
    _record(
        actor,
        audit.Action.APPROVAL_CHANGE_SUBMITTED,
        kind,
        item,
        "bekleyen değişikliği düzenlendi",
        before=before,
        after=current.payload,
    )
    return item


# --- state changes ------------------------------------------------------------------


def _move(  # noqa: PLR0913
    actor: Any,
    kind: Kind,
    item_id: int,
    *,
    allowed_from: tuple[str, ...],
    to: ApprovalStatus,
    action: audit.Action,
    summary: str,
    only_submitter: bool = False,
    reason: str = "",
) -> Any:
    with transaction.atomic():
        item = _locked(kind, item_id)
        if item.status not in allowed_from:
            msg = f"Bu işlem şu durumdayken yapılamaz: {item.status}."
            raise ConflictError(msg, code="approval_state")
        if only_submitter and item.submitted_by_id != actor.pk:
            msg = "Yalnızca gönderen kişi geri çekebilir."
            raise ForbiddenError(msg, code="approval_not_submitter")
        before = item.status
        item.status = to
        if to == ApprovalStatus.PUBLISHED:
            item.published_at = timezone.now()
            item.rejection_reason = ""
        if action == audit.Action.APPROVAL_SUBMITTED:
            item.submitted_by = actor
            item.submitted_at = timezone.now()
            item.rejection_reason = ""
        if action == audit.Action.APPROVAL_REJECTED:
            item.rejection_reason = reason
        item.save()
        if to == ApprovalStatus.PUBLISHED:
            _save_version(kind, item, actor)
        _record(
            actor,
            action,
            kind,
            item,
            summary,
            before={"status": before},
            after={"status": to.value, **({"reason": reason} if reason else {})},
        )
    return item


def submit(actor: Any, kind: Kind, item_id: int) -> Any:
    """Draft to pending approval, for the Owner to approve or reject."""
    _require_edit(actor, kind)
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.DRAFT,),
        to=ApprovalStatus.PENDING,
        action=audit.Action.APPROVAL_SUBMITTED,
        summary="onaya gönderildi",
    )


def withdraw(actor: Any, kind: Kind, item_id: int) -> Any:
    """Pending approval back to draft, by whoever submitted it."""
    _require_edit(actor, kind)
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.PENDING,),
        to=ApprovalStatus.DRAFT,
        action=audit.Action.APPROVAL_WITHDRAWN,
        summary="onaydan geri çekildi",
        only_submitter=True,
    )


def approve(actor: Any, kind: Kind, item_id: int) -> Any:
    """Pending approval to published. For a published item, applies its pending
    change instead."""
    _require_manage(actor, kind)
    if _has_live_change(kind, item_id):
        return _approve_change(actor, kind, item_id)
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.PENDING,),
        to=ApprovalStatus.PUBLISHED,
        action=audit.Action.APPROVAL_APPROVED,
        summary="onaylandı ve yayınlandı",
    )


def reject(actor: Any, kind: Kind, item_id: int, reason: str = "") -> Any:
    """Pending approval back to draft; the reason is shown to the submitter. For a
    published item, drops its pending change instead."""
    _require_manage(actor, kind)
    reason = reason.strip()
    if len(reason) > REASON_MAX:
        _refuse("reason", f"Gerekçe en fazla {REASON_MAX} karakter olabilir.")
    if _has_live_change(kind, item_id):
        return _reject_change(actor, kind, item_id, reason)
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.PENDING,),
        to=ApprovalStatus.DRAFT,
        action=audit.Action.APPROVAL_REJECTED,
        summary="reddedildi",
        reason=reason,
    )


def publish(actor: Any, kind: Kind, item_id: int) -> Any:
    """Draft or unpublished to published; Owner-only (Staff submit instead)."""
    _require_manage(actor, kind)
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.DRAFT, ApprovalStatus.UNPUBLISHED),
        to=ApprovalStatus.PUBLISHED,
        action=audit.Action.APPROVAL_PUBLISHED,
        summary="yayınlandı",
    )


def unpublish(actor: Any, kind: Kind, item_id: int) -> Any:
    """Published to unpublished: off the site, kept."""
    _require_manage(actor, kind)
    if _has_live_change(kind, item_id):
        msg = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
        raise ConflictError(msg, code="approval_has_pending_change")
    return _move(
        actor,
        kind,
        item_id,
        allowed_from=(ApprovalStatus.PUBLISHED,),
        to=ApprovalStatus.UNPUBLISHED,
        action=audit.Action.APPROVAL_UNPUBLISHED,
        summary="yayından kaldırıldı",
    )


def _has_live_change(kind: Kind, item_id: int) -> bool:
    return (
        has_pending_change(kind, item_id)
        and kind.model.objects.filter(
            pk=item_id,
            status=ApprovalStatus.PUBLISHED,
        ).exists()
    )


@transaction.atomic
def _approve_change(actor: Any, kind: Kind, item_id: int) -> Any:
    item = _locked(kind, item_id)
    current = pending_change(kind, item.pk)
    # Revalidate: a slug or reference may have gone since Staff made the edit.
    cleaned = kind.clean(dict(current.payload), item)
    before = jsonable(kind, item)
    for field, value in cleaned.items():
        setattr(item, field, value)
    item.rejection_reason = ""
    item.save()
    current.delete()
    _save_version(kind, item, actor)
    _record(
        actor,
        audit.Action.APPROVAL_CHANGE_APPROVED,
        kind,
        item,
        "değişikliği onaylandı",
        before=before,
        after=jsonable(kind, item),
    )
    return item


@transaction.atomic
def _reject_change(actor: Any, kind: Kind, item_id: int, reason: str) -> Any:
    item = _locked(kind, item_id)
    current = pending_change(kind, item.pk)
    dropped = dict(current.payload)
    # The change is gone, so its submitter reads the reason on the item.
    item.submitted_by = current.submitted_by
    item.submitted_at = current.updated_at
    item.rejection_reason = reason
    item.save()
    current.delete()
    _record(
        actor,
        audit.Action.APPROVAL_CHANGE_REJECTED,
        kind,
        item,
        "değişikliği reddedildi",
        before=dropped,
        after={"reason": reason} if reason else None,
    )
    return item
