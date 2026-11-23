"""Write business events to the append-only audit log."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Any

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.staff.models import AppendOnlyError
from store_backend.staff.models import AuditEntry

if TYPE_CHECKING:
    import datetime as dt

    from django.db.models import Model
    from django.db.models import QuerySet

__all__ = ["Action", "AppendOnlyError", "record"]


class Action(StrEnum):
    LOGIN = "login"
    MARK_PAID = "mark_paid"
    CANCEL = "cancel"
    REFUND = "refund"
    SHIP = "ship"
    DELIVER = "deliver"
    RETURN_REQUEST = "return_request"
    RETURN_APPROVED = "return_approved"
    RETURN_REJECTED = "return_rejected"
    RETURN_RECEIVED = "return_received"
    ORDER_EDIT = "order_edit"
    MESSAGE_RESEND = "message_resend"
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
    CATALOGUE_IMPORT = "catalogue_import"
    ACCOUNT_DELETION = "account_deletion"
    LEGAL_PAGE_PUBLISHED = "legal_page_published"
    LEGAL_SLUG_CHANGED = "legal_slug_changed"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    REVIEW_DELETED = "review_deleted"
    CATEGORY_MOVED = "category_moved"
    CATEGORY_DELETED = "category_deleted"
    BRAND_DELETED = "brand_deleted"
    SUPPLIER_DELETED = "supplier_deleted"
    COLOUR_DELETED = "colour_deleted"
    VARIANT_DELETED = "variant_deleted"
    PRODUCT_DELETED = "product_deleted"
    VOUCHER_CREATED = "voucher_created"
    VOUCHER_EDITED = "voucher_edited"
    VOUCHER_DEACTIVATED = "voucher_deactivated"
    VOUCHER_ACTIVATED = "voucher_activated"
    VOUCHER_DELETED = "voucher_deleted"
    MENU_SAVED = "menu_saved"
    COLLECTION_CREATED = "collection_created"
    COLLECTION_EDITED = "collection_edited"
    COLLECTION_SUBMITTED = "collection_submitted"
    COLLECTION_WITHDRAWN = "collection_withdrawn"
    COLLECTION_APPROVED = "collection_approved"
    COLLECTION_REJECTED = "collection_rejected"
    COLLECTION_CHANGE_SUBMITTED = "collection_change_submitted"
    COLLECTION_CHANGE_APPROVED = "collection_change_approved"
    COLLECTION_CHANGE_REJECTED = "collection_change_rejected"
    COLLECTION_PUBLISHED = "collection_published"
    COLLECTION_UNPUBLISHED = "collection_unpublished"
    COLLECTION_DELETED = "collection_deleted"
    CAMPAIGN_CREATED = "campaign_created"
    CAMPAIGN_EDITED = "campaign_edited"
    CAMPAIGN_ACTIVATED = "campaign_activated"
    CAMPAIGN_DEACTIVATED = "campaign_deactivated"
    CAMPAIGN_DELETED = "campaign_deleted"
    SIZE_GUIDE_EDITED = "size_guide_edited"
    FIT_NOTE_EDITED = "fit_note_edited"
    APPROVAL_SUBMITTED = "approval_submitted"
    APPROVAL_WITHDRAWN = "approval_withdrawn"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_PUBLISHED = "approval_published"
    APPROVAL_UNPUBLISHED = "approval_unpublished"
    APPROVAL_EDITED = "approval_edited"
    APPROVAL_DELETED = "approval_deleted"
    APPROVAL_CHANGE_SUBMITTED = "approval_change_submitted"
    APPROVAL_CHANGE_APPROVED = "approval_change_approved"
    APPROVAL_CHANGE_REJECTED = "approval_change_rejected"
    CONTACT_REPLY_SUBMITTED = "contact_reply_submitted"
    CONTACT_REPLY_APPROVED = "contact_reply_approved"
    CONTACT_REPLY_REJECTED = "contact_reply_rejected"
    CONTACT_REPLY_SENT = "contact_reply_sent"


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


def search(
    *,
    actor: int | None = None,
    action: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> QuerySet[AuditEntry]:
    """Entries newest first. The dates are inclusive days in the shop's time zone."""
    if action and action not in {a.value for a in Action}:
        msg = "Geçersiz işlem."
        raise UnprocessableError(msg, fields={"action": [msg]})
    entries = AuditEntry.objects.all()
    if actor is not None:
        entries = entries.filter(actor_id=actor)
    if action:
        entries = entries.filter(action=action)
    if date_from:
        entries = entries.filter(created_at__date__gte=date_from)
    if date_to:
        entries = entries.filter(created_at__date__lte=date_to)
    return entries.order_by("-created_at", "-id")


def get(entry_id: int) -> AuditEntry:
    entry = AuditEntry.objects.filter(pk=entry_id).first()
    if entry is None:
        raise NotFoundError
    return entry
