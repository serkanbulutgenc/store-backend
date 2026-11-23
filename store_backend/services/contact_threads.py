# ruff: noqa: EM101
"""The contact form: categories the Owner manages and the threads Shoppers start.

Open to Guests and signed-in Customers. Abuse limits are a per-IP rate limit and a
hidden honeypot field; neither stores anything. The Shopper gets an automatic
acknowledgement email through the outbox.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery
from django.contrib.postgres.search import SearchVector
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from oscar.apps.order.models import Order

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.api.ratelimit import Rate
from store_backend.api.ratelimit import count_attempt
from store_backend.services import audit
from store_backend.services import outbox
from store_backend.services import tracking
from store_backend.shop.models import ContactCategory
from store_backend.shop.models import ContactMessage
from store_backend.shop.models import ContactMessageKind
from store_backend.shop.models import ContactReplyState
from store_backend.shop.models import ContactThread
from store_backend.shop.models import ContactThreadStatus
from store_backend.staff.permissions import Perm

if TYPE_CHECKING:
    from store_backend.users.models import User

NAME_MAX = 100
MESSAGE_MAX = 5000
ORDER_NUMBER_MAX = 50


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


# -- categories (Owner) ------------------------------------------------------------


def categories(*, only_active: bool = False):
    """Categories with their thread counts, in the Owner's order."""
    # Meta.ordering is ignored once a query groups, so order explicitly.
    rows = ContactCategory.objects.annotate(thread_count=Count("threads")).order_by(
        "position",
        "pk",
    )
    return rows.filter(is_active=True) if only_active else rows


def _clean_name(name: str, *, exclude: int | None = None) -> str:
    name = name.strip()
    if not name or len(name) > NAME_MAX:
        raise _invalid("name", f"Ad 1 ile {NAME_MAX} karakter arasında olmalı.")
    same = ContactCategory.objects.annotate(lowered=Lower("name")).filter(
        lowered=name.lower(),
    )
    if exclude is not None:
        same = same.exclude(pk=exclude)
    if same.exists():
        raise _invalid("name", "Bu adla bir kategori var.")
    return name


def create_category(*, name: str) -> ContactCategory:
    last = ContactCategory.objects.order_by("-position").first()
    return ContactCategory.objects.create(
        name=_clean_name(name),
        position=(last.position + 1) if last else 0,
    )


def update_category(
    category_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    position: int | None = None,
) -> ContactCategory:
    category = _category(category_id)
    if name is not None:
        category.name = _clean_name(name, exclude=category.pk)
    if is_active is not None:
        category.is_active = is_active
    if position is not None:
        category.position = position
    category.save()
    return category


def delete_category(category_id: int) -> None:
    category = _category(category_id)
    if category.threads.exists():
        msg = "Bu kategoride yazışmalar var; silmek yerine kapatın."
        raise ConflictError(msg, code="contact_category_in_use")
    category.delete()


def _category(category_id: int) -> ContactCategory:
    category = ContactCategory.objects.filter(pk=category_id).first()
    if category is None:
        raise NotFoundError
    return category


# -- the form (Shoppers) -----------------------------------------------------------


def _linked_order(raw: str) -> Order | None:
    """The Order the typed number names, or None. Never says which it was."""
    if not raw or not tracking.is_order_number(raw):
        return None
    return Order.objects.filter(number=raw).first()


@transaction.atomic
def submit(  # noqa: PLR0913
    *,
    category_id: int,
    name: str,
    email: str,
    message: str,
    order_number: str = "",
    honeypot: str = "",
    ip: str | None,
    user: User | None = None,
) -> ContactThread | None:
    """Store a thread with status New and queue the acknowledgement. Returns None when
    the honeypot was filled: the caller answers as if it worked, and nothing is kept."""
    count_attempt("contact", ip, Rate(*settings.CONTACT_RATE_LIMIT))
    if honeypot.strip():
        return None
    name = name.strip()
    email = email.strip()
    message = message.strip()
    number = tracking.normalise_number(order_number)
    if not name or len(name) > NAME_MAX:
        raise _invalid("name", f"Ad 1 ile {NAME_MAX} karakter arasında olmalı.")
    try:
        validate_email(email)
    except ValidationError:
        raise _invalid("email", "Geçerli bir e-posta adresi girin.") from None
    if not message or len(message) > MESSAGE_MAX:
        raise _invalid(
            "message",
            f"Mesaj 1 ile {MESSAGE_MAX} karakter arasında olmalı.",
        )
    if len(number) > ORDER_NUMBER_MAX:
        raise _invalid("order_number", "Sipariş numarası çok uzun.")
    category = ContactCategory.objects.filter(pk=category_id, is_active=True).first()
    if category is None:
        raise _invalid("category", "Geçerli bir konu seçin.")

    thread = ContactThread.objects.create(
        category=category,
        name=name,
        email=email,
        user=user if user is not None and user.is_authenticated else None,
        order_number=number,
        order=_linked_order(number),
    )
    ContactMessage.objects.create(
        thread=thread,
        kind=ContactMessageKind.SHOPPER,
        body=message,
    )
    outbox.enqueue(
        "contact_acknowledgement",
        to=email,
        context={"name": name, "category": category.name},
    )
    return thread


# -- threads and replies (Owner and Staff) -----------------------------------------
# Staff write a draft and submit it; only the Owner approves (as written or edited),
# rejects with a note, or replies directly. Only an approved reply reaches the outbox,
# and the text sent is the text the thread keeps.

REPLY_MAX = 5000
SNIPPET_MAX = 140
SEARCH_CONFIG = "turkish"
NOTE_MAX = 500


def _is_owner(actor: User) -> bool:
    return actor.has_perm(Perm.MANAGE_CONTACT.permission_name)


def _require_owner(actor: User) -> None:
    if not _is_owner(actor):
        msg = "Yanıtları yalnızca sahip onaylayabilir."
        raise ForbiddenError(msg, code="contact_owner_only")


def threads(  # noqa: PLR0913
    *,
    q: str | None = None,
    status: str | None = None,
    assignee_id: int | None = None,
    category_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_pending_reply: bool | None = None,
):
    """Threads, latest activity first, narrowed by every filter given.

    `q` searches the Shopper's name, email and order number, the category name, and
    the text of every message (Turkish full-text). An anonymised thread has none of
    that left, so `q` never matches it; it still shows up under the other filters.
    `date_from` and `date_to` bound the last activity, both days included.
    """
    rows = ContactThread.objects.select_related(
        "category",
        "assignee",
        "order",
    ).annotate(
        pending_reply_count=Count(
            "messages",
            filter=Q(messages__state=ContactReplyState.PENDING),
        ),
    )
    # Meta.ordering is ignored once a query groups, so order explicitly.
    rows = rows.order_by("-last_activity_at", "-pk")
    if status is not None:
        if status not in ContactThreadStatus.values:
            raise _invalid("status", "Bilinmeyen durum.")
        rows = rows.filter(status=status)
    if assignee_id is not None:
        rows = rows.filter(assignee_id=assignee_id)
    if category_id is not None:
        rows = rows.filter(category_id=category_id)
    if date_from:
        rows = rows.filter(last_activity_at__date__gte=_day("date_from", date_from))
    if date_to:
        rows = rows.filter(last_activity_at__date__lte=_day("date_to", date_to))
    if has_pending_reply is not None:
        rows = (
            rows.filter(pending_reply_count__gt=0)
            if has_pending_reply
            else rows.filter(pending_reply_count=0)
        )
    text = (q or "").strip()
    if text:
        rows = rows.filter(anonymised_at__isnull=True).filter(_matches(text))
    return rows


def _day(field: str, raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _invalid(field, "Tarihi GG.AA.YYYY değil, YYYY-AA-GG yazın.") from None


def _matches(text: str) -> Q:
    in_messages = (
        ContactMessage.objects.annotate(
            vector=SearchVector("body", config=SEARCH_CONFIG),
        )
        .filter(vector=SearchQuery(text, config=SEARCH_CONFIG, search_type="websearch"))
        .values("thread_id")
    )
    return (
        Q(name__icontains=text)
        | Q(email__icontains=text)
        | Q(order_number__icontains=text)
        | Q(category__name__icontains=text)
        | Q(pk__in=in_messages)
    )


def snippet(found: ContactThread) -> str:
    """The first words of what the Shopper wrote; empty once anonymised."""
    if found.anonymised_at is not None:
        return ""
    first = found.messages.filter(kind=ContactMessageKind.SHOPPER).first()
    text = first.body if first else ""
    return text if len(text) <= SNIPPET_MAX else text[: SNIPPET_MAX - 1].rstrip() + "…"


def thread(thread_id: int) -> ContactThread:
    found = (
        ContactThread.objects.select_related("category", "assignee", "order")
        .filter(pk=thread_id)
        .first()
    )
    if found is None:
        raise NotFoundError
    return found


def _locked_thread(thread_id: int) -> ContactThread:
    found = ContactThread.objects.select_for_update().filter(pk=thread_id).first()
    if found is None:
        raise NotFoundError
    return found


def _reply(reply_id: int) -> ContactMessage:
    found = (
        ContactMessage.objects.select_for_update()
        .filter(pk=reply_id, kind=ContactMessageKind.REPLY)
        .first()
    )
    if found is None:
        raise NotFoundError
    return found


@transaction.atomic
def update_thread(
    actor: User,
    thread_id: int,
    *,
    status: str | None = None,
    assignee_id: int | None = None,
    clear_assignee: bool = False,
) -> ContactThread:
    """Set the status and/or the assignee. Anyone with access to threads may; the
    assignee must be someone who has it too."""
    found = _locked_thread(thread_id)
    if status is not None:
        if status not in ContactThreadStatus.values:
            raise _invalid("status", "Bilinmeyen durum.")
        found.status = status
    if clear_assignee:
        found.assignee = None
    elif assignee_id is not None:
        person = get_user_model().objects.filter(pk=assignee_id, is_active=True).first()
        if person is None or not person.has_perm(Perm.EDIT_CONTACT.permission_name):
            raise _invalid("assignee_id", "Bu kişiye yazışma atanamaz.")
        found.assignee = person
    found.save()
    return thread(thread_id)


def _clean_reply(body: str) -> str:
    body = body.strip()
    if not body or len(body) > REPLY_MAX:
        raise _invalid("body", f"Yanıt 1 ile {REPLY_MAX} karakter arasında olmalı.")
    return body


def _open_for_reply(found: ContactThread) -> None:
    if found.anonymised_at is not None:
        msg = "Anonimleştirilen yazışmaya yanıt verilemez."
        raise ConflictError(msg, code="contact_thread_anonymised")


def _touch(found: ContactThread, *, status: str | None = None) -> None:
    found.last_activity_at = timezone.now()
    if status:
        found.status = status
    found.save(update_fields=["last_activity_at", "status"])


@transaction.atomic
def write_reply(actor: User, thread_id: int, *, body: str) -> ContactMessage:
    """Staff: save a draft. The Owner: the reply is sent straight away, with no second
    approval."""
    found = _locked_thread(thread_id)
    _open_for_reply(found)
    body = _clean_reply(body)
    reply = ContactMessage.objects.create(
        thread=found,
        kind=ContactMessageKind.REPLY,
        body=body,
        state=ContactReplyState.DRAFT,
        author=actor,
    )
    if _is_owner(actor):
        _send(reply, found, actor, action=audit.Action.CONTACT_REPLY_SENT)
    elif found.status == ContactThreadStatus.NEW:
        _touch(found, status=ContactThreadStatus.IN_PROGRESS)
    return reply


@transaction.atomic
def edit_reply(actor: User, reply_id: int, *, body: str) -> ContactMessage:
    """Change a draft or a rejected reply: its author, or the Owner. A rejected reply
    becomes a draft again, to be submitted anew."""
    reply = _reply(reply_id)
    if reply.state not in (ContactReplyState.DRAFT, ContactReplyState.REJECTED):
        msg = "Yalnızca taslak veya reddedilen yanıt düzenlenebilir."
        raise ConflictError(msg, code="contact_reply_state")
    if reply.author_id != actor.pk and not _is_owner(actor):
        raise ForbiddenError
    reply.body = _clean_reply(body)
    reply.state = ContactReplyState.DRAFT
    reply.save(update_fields=["body", "state"])
    return reply


@transaction.atomic
def submit_reply(actor: User, reply_id: int) -> ContactMessage:
    """Send a draft (or a rejected reply, revised) to the Owner. Only its author."""
    reply = _reply(reply_id)
    if reply.author_id != actor.pk:
        raise ForbiddenError
    if reply.state not in (ContactReplyState.DRAFT, ContactReplyState.REJECTED):
        msg = "Bu yanıt zaten onaya gönderildi veya gönderildi."
        raise ConflictError(msg, code="contact_reply_state")
    reply.state = ContactReplyState.PENDING
    reply.rejection_note = ""
    reply.save(update_fields=["state", "rejection_note"])
    audit.record(
        actor,
        audit.Action.CONTACT_REPLY_SUBMITTED,
        target=reply.thread,
        summary=f"Yanıt onaya gönderildi (yazışma {reply.thread_id})",
    )
    return reply


@transaction.atomic
def approve_reply(
    actor: User,
    reply_id: int,
    *,
    body: str | None = None,
) -> ContactMessage:
    """The Owner sends a waiting reply as written, or with `body` as the edited text.
    The text sent is the text the thread keeps; the Staff draft is kept beside it."""
    _require_owner(actor)
    reply = _reply(reply_id)
    if reply.state != ContactReplyState.PENDING:
        msg = "Yalnızca onay bekleyen yanıt onaylanabilir."
        raise ConflictError(msg, code="contact_reply_state")
    found = _locked_thread(reply.thread_id)
    _open_for_reply(found)
    if body is not None:
        edited = _clean_reply(body)
        if edited != reply.body:
            reply.drafted_body = reply.body
            reply.body = edited
    _send(reply, found, actor, action=audit.Action.CONTACT_REPLY_APPROVED)
    return reply


@transaction.atomic
def reject_reply(actor: User, reply_id: int, *, note: str = "") -> ContactMessage:
    """The Owner sends a waiting reply back to Staff with a note they can read."""
    _require_owner(actor)
    reply = _reply(reply_id)
    if reply.state != ContactReplyState.PENDING:
        msg = "Yalnızca onay bekleyen yanıt reddedilebilir."
        raise ConflictError(msg, code="contact_reply_state")
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise _invalid("note", f"Not en fazla {NOTE_MAX} karakter olabilir.")
    reply.state = ContactReplyState.REJECTED
    reply.rejection_note = note
    reply.decided_by = actor
    reply.save(update_fields=["state", "rejection_note", "decided_by"])
    audit.record(
        actor,
        audit.Action.CONTACT_REPLY_REJECTED,
        target=reply.thread,
        summary=f"Yanıt reddedildi (yazışma {reply.thread_id})",
    )
    return reply


def _send(
    reply: ContactMessage,
    found: ContactThread,
    actor: User,
    *,
    action: audit.Action,
) -> None:
    """Queue the email and mark the reply sent: the one way a reply is delivered."""
    outbox.enqueue(
        "contact_reply",
        to=found.email,
        context={"name": found.name, "body": reply.body},
    )
    reply.state = ContactReplyState.SENT
    reply.decided_by = actor
    reply.sent_at = timezone.now()
    reply.save(
        update_fields=["body", "drafted_body", "state", "decided_by", "sent_at"],
    )
    _touch(found, status=ContactThreadStatus.ANSWERED)
    audit.record(
        actor,
        action,
        target=found,
        summary=f"Yanıt gönderildi (yazışma {found.pk})",
    )
