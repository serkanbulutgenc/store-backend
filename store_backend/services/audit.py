"""Write business events to the append-only audit log."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Any

from store_backend.staff.models import AppendOnlyError
from store_backend.staff.models import AuditEntry

if TYPE_CHECKING:
    from django.db.models import Model

__all__ = ["Action", "AppendOnlyError", "record"]


class Action(StrEnum):
    LOGIN = "login"
    MARK_PAID = "mark_paid"
    CANCEL = "cancel"
    REFUND = "refund"
    SHIP = "ship"
    DELIVER = "deliver"
    RETURN_REQUEST = "return_request"
    RETURN_RECEIVED = "return_received"
    ORDER_EDIT = "order_edit"
    STOCK_ADJUSTMENT = "stock_adjustment"
    PRICE_CHANGE = "price_change"
    SETTINGS_CHANGE = "settings_change"
    STAFF_INVITED = "staff_invited"
    STAFF_DEACTIVATED = "staff_deactivated"
    ROLE_CHANGE = "role_change"
    PHOTO_UPLOAD = "photo_upload"
    PHOTO_DELETE = "photo_delete"
    PHOTO_EDIT = "photo_edit"
    INVOICE_EDIT = "invoice_edit"
    EXPORT_RUN = "export_run"
    ACCOUNT_DELETION = "account_deletion"


def record(  # noqa: PLR0913
    actor: Model | None,
    action: Action,
    *,
    target: Model | tuple[str, Any],
    summary: str = "",
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEntry:
    """Append one entry. `actor` is None for system actions; `target` is a model
    instance or a `(type, id)` pair for things without one (such as Settings).
    """
    if isinstance(target, tuple):
        target_type, target_id = target
    else:
        target_type, target_id = target._meta.label_lower, target.pk  # noqa: SLF001
    return AuditEntry.objects.create(
        actor=actor,  # type: ignore[misc]
        actor_email=getattr(actor, "email", "") or "",
        action=str(action),
        target_type=str(target_type),
        target_id=str(target_id),
        summary=summary,
        before=before,
        after=after,
    )
