"""The contact service's Dashboard API: categories (Owner only), threads and replies.

Owner and Staff see every thread and may draft a reply, set the status and the
assignee. Only the Owner approves, edits, rejects or sends a reply.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from http import HTTPStatus

from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.api.permissions import requires_owner
from store_backend.catalog.collections_api import PersonOut  # noqa: TC001
from store_backend.services import contact_threads
from store_backend.staff.permissions import Perm

router = Router(tags=["contact"])


class ContactCategoryRow(Schema):
    id: int
    name: str
    is_active: bool
    position: int
    thread_count: int = 0


class ContactCategoryIn(Schema):
    name: str = Field(max_length=200)


class ContactCategoryPatch(Schema):
    name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    position: int | None = Field(default=None, ge=0, le=32767)


def _row(category) -> dict:
    return {
        "id": category.pk,
        "name": category.name,
        "is_active": category.is_active,
        "position": category.position,
        "thread_count": getattr(category, "thread_count", None)
        or category.threads.count(),
    }


@router.get(
    "/contact-categories",
    auth=requires_owner(),
    operation_id="contact_categories_list",
    response=list[ContactCategoryRow],
)
def list_categories(request):
    """Every category, inactive ones too, with how many threads use it."""
    return [_row(c) for c in contact_threads.categories()]


@router.post(
    "/contact-categories",
    auth=requires_owner(),
    operation_id="contact_categories_create",
    response={HTTPStatus.CREATED: ContactCategoryRow},
)
def create_category(request, data: ContactCategoryIn):
    """Add a category at the end. 422 for an empty or repeated name."""
    return HTTPStatus.CREATED, _row(contact_threads.create_category(name=data.name))


@router.patch(
    "/contact-categories/{category_id}",
    auth=requires_owner(),
    operation_id="contact_categories_update",
    response=ContactCategoryRow,
)
def update_category(request, category_id: int, data: ContactCategoryPatch):
    """Rename, deactivate or reorder. Existing threads keep the category."""
    return _row(
        contact_threads.update_category(
            category_id,
            **data.dict(exclude_unset=True),
        ),
    )


@router.delete(
    "/contact-categories/{category_id}",
    auth=requires_owner(),
    operation_id="contact_categories_delete",
    response={HTTPStatus.NO_CONTENT: None},
)
def delete_category(request, category_id: int):
    """Delete an unused category. 409 `contact_category_in_use` once it has threads:
    deactivate it instead."""
    contact_threads.delete_category(category_id)
    return HTTPStatus.NO_CONTENT, None


# -- threads and replies -----------------------------------------------------------


class ThreadRow(Schema):
    id: int
    category: str
    name: str
    email: str
    order_number: str
    status: str
    assignee: PersonOut | None
    created_at: datetime
    last_activity_at: datetime
    anonymised: bool


class ThreadSummary(ThreadRow):
    snippet: str
    pending_reply_count: int

    @staticmethod
    def resolve_category(obj) -> str:
        return obj.category.name

    @staticmethod
    def resolve_assignee(obj) -> dict | None:
        return _person(obj.assignee)

    @staticmethod
    def resolve_anonymised(obj) -> bool:
        return obj.anonymised_at is not None

    @staticmethod
    def resolve_snippet(obj) -> str:
        return contact_threads.snippet(obj)


class ReplyOut(Schema):
    id: int
    kind: str
    body: str
    state: str
    author: PersonOut | None
    decided_by: PersonOut | None
    rejection_note: str
    drafted_body: str
    sent_at: datetime | None
    created_at: datetime


class ThreadOut(ThreadRow):
    order_linked: bool
    messages: list[ReplyOut]


class ThreadPatch(Schema):
    status: str | None = None
    assignee_id: int | None = None


class ReplyIn(Schema):
    body: str = Field(max_length=20000)


class ReplyApproveIn(Schema):
    # The Owner's edited text; leave it out to send the draft as written.
    body: str | None = Field(default=None, max_length=20000)


class ReplyRejectIn(Schema):
    note: str = Field(default="", max_length=2000)


def _person(user) -> dict | None:
    return {"id": user.pk, "name": user.name or user.email} if user else None


def _thread_row(thread) -> dict:
    return {
        "id": thread.pk,
        "category": thread.category.name,
        "name": thread.name,
        "email": thread.email,
        "order_number": thread.order_number,
        "status": thread.status,
        "assignee": _person(thread.assignee),
        "created_at": thread.created_at,
        "last_activity_at": thread.last_activity_at,
        "anonymised": thread.anonymised_at is not None,
    }


def _message(message) -> dict:
    return {
        "id": message.pk,
        "kind": message.kind,
        "body": message.body,
        "state": message.state,
        "author": _person(message.author),
        "decided_by": _person(message.decided_by),
        "rejection_note": message.rejection_note,
        "drafted_body": message.drafted_body,
        "sent_at": message.sent_at,
        "created_at": message.created_at,
    }


def _thread_out(thread) -> dict:
    messages = thread.messages.select_related("author", "decided_by")
    return {
        **_thread_row(thread),
        "order_linked": thread.order_id is not None,
        "messages": [_message(m) for m in messages],
    }


@router.get(
    "/contact-threads",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_threads_list",
    response=list[ThreadSummary],
)
@paginate(PageOffsetPagination)
def list_threads(  # noqa: PLR0913, PLR0917
    request,
    q: str | None = None,
    status: str | None = None,
    assignee_id: int | None = None,
    category_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_pending_reply: bool | None = None,  # noqa: FBT001
):
    """Threads, latest activity first, as a page. Filters combine.

    `q` searches name, email, order number, category and message text (anonymised
    threads never match it). `date_from`/`date_to` (YYYY-MM-DD) bound the last activity,
    both days included. `has_pending_reply` finds threads with a reply waiting for the
    Owner.
    """
    return contact_threads.threads(
        q=q,
        status=status,
        assignee_id=assignee_id,
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
        has_pending_reply=has_pending_reply,
    )


@router.get(
    "/contact-threads/{thread_id}",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_threads_get",
    response=ThreadOut,
)
def get_thread(request, thread_id: int):
    """The Shopper's message, then drafts, decisions and sent replies in order."""
    return _thread_out(contact_threads.thread(thread_id))


@router.patch(
    "/contact-threads/{thread_id}",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_threads_update",
    response=ThreadOut,
)
def update_thread(request, thread_id: int, data: ThreadPatch):
    """Set the status and/or the assignee. `assignee_id: null` clears the assignee."""
    given = data.model_dump(exclude_unset=True)
    contact_threads.update_thread(
        request.auth,
        thread_id,
        status=given.get("status"),
        assignee_id=given.get("assignee_id"),
        clear_assignee="assignee_id" in given and given["assignee_id"] is None,
    )
    return _thread_out(contact_threads.thread(thread_id))


@router.post(
    "/contact-threads/{thread_id}/replies",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_replies_create",
    response={HTTPStatus.CREATED: ReplyOut},
)
def write_reply(request, thread_id: int, data: ReplyIn):
    """Staff: a draft (state `draft`). The Owner: sent at once (state `sent`)."""
    reply = contact_threads.write_reply(request.auth, thread_id, body=data.body)
    return HTTPStatus.CREATED, _message(reply)


@router.patch(
    "/contact-replies/{reply_id}",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_replies_update",
    response=ReplyOut,
)
def edit_reply(request, reply_id: int, data: ReplyIn):
    """Change a draft or a rejected reply (its author, or the Owner)."""
    return _message(contact_threads.edit_reply(request.auth, reply_id, body=data.body))


@router.post(
    "/contact-replies/{reply_id}/submit",
    auth=requires(Perm.EDIT_CONTACT),
    operation_id="contact_replies_submit",
    response=ReplyOut,
)
def submit_reply(request, reply_id: int):
    """Send the author's draft to the Owner for approval."""
    return _message(contact_threads.submit_reply(request.auth, reply_id))


@router.post(
    "/contact-replies/{reply_id}/approve",
    auth=requires(Perm.MANAGE_CONTACT),
    operation_id="contact_replies_approve",
    response=ReplyOut,
)
def approve_reply(request, reply_id: int, data: ReplyApproveIn):
    """Owner: send the waiting reply as written, or with `body` as the edited text."""
    return _message(
        contact_threads.approve_reply(request.auth, reply_id, body=data.body),
    )


@router.post(
    "/contact-replies/{reply_id}/reject",
    auth=requires(Perm.MANAGE_CONTACT),
    operation_id="contact_replies_reject",
    response=ReplyOut,
)
def reject_reply(request, reply_id: int, data: ReplyRejectIn):
    """Owner: send the waiting reply back to Staff with a note they can read."""
    return _message(
        contact_threads.reject_reply(request.auth, reply_id, note=data.note),
    )
