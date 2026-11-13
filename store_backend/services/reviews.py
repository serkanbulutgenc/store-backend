"""Reviews: verified-buyer star ratings with moderated text (ADR 0004)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Avg
from django.db.models import Count
from django.db.models import Sum
from django.utils import timezone
from oscar.apps.order.models import Line

from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import returns
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnStatus
from store_backend.shop.models import Review
from store_backend.shop.models import ReviewStatus

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from store_backend.users.models import User

BODY_MAX = 2000
PAGE_SIZE = 10
FALLBACK_NAME = "Müşteri"

_HTML = re.compile(r"<\s*/?\s*[A-Za-z!?]")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Returns that took the goods back for good; a request or a rejection did not.
_RETURNED = (ReturnStatus.APPROVED, ReturnStatus.RECEIVED, ReturnStatus.REFUNDED)

NOT_DELIVERED = (
    "review_not_delivered",
    "Bu ürünü değerlendirmek için teslim edilmiş bir siparişiniz olmalı.",
)
FULLY_RETURNED = (
    "review_fully_returned",
    "Bu ürünü tamamen iade ettiğiniz için değerlendirme yapamazsınız.",
)


def display_name(user) -> str:
    """ "Ayşe K.": the first name and the initial of the last one."""
    parts = (user.name or "").split()
    if not parts:
        return FALLBACK_NAME
    if len(parts) == 1:
        return parts[0]
    initial = parts[-1][0]
    initial = "İ" if initial == "i" else initial.upper()
    return f"{parts[0]} {initial}."


def clean_body(body: str) -> str:
    """The text to store: no HTML (refused), links and images stripped, trimmed."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > BODY_MAX:
        msg = f"Yorum en fazla {BODY_MAX} karakter olabilir."
        raise UnprocessableError(msg, fields={"body": [msg]})
    if _HTML.search(text):
        msg = "Yorumda HTML kullanılamaz."
        raise UnprocessableError(msg, fields={"body": [msg]})
    text = _IMAGE.sub("", text)
    return _LINK.sub(r"\1", text).strip()


# --- eligibility ---------------------------------------------------------------


def blocked_reason(user: User, product_id: int) -> tuple[str, str] | None:
    """Why the Shopper may not review the Product, as `(code, message)`, or None."""
    lines = list(
        Line.objects.filter(
            order__user=user,
            order__status__in=returns.RETURNABLE_STATUSES,
            product__parent_id=product_id,
        ),
    )
    if not lines:
        return NOT_DELIVERED
    taken_back = {
        row["order_line_id"]: row["total"]
        for row in ReturnLine.objects.filter(
            order_line__in=lines,
            request__status__in=_RETURNED,
        )
        .values("order_line_id")
        .annotate(total=Sum("quantity"))
    }
    if all(taken_back.get(line.pk, 0) >= line.quantity for line in lines):
        return FULLY_RETURNED
    return None


def _published_product(product_id: int) -> None:
    if not ProductProfile.objects.filter(
        product_id=product_id,
        status=ProductStatus.PUBLISHED,
    ).exists():
        raise NotFoundError


def mine(user: User, product_id: int) -> Review | None:
    return Review.objects.filter(user=user, product_id=product_id).first()


def state_for(user: User, product_id: int) -> dict:
    blocked = blocked_reason(user, product_id)
    return {
        "can_review": blocked is None,
        "reason": blocked[0] if blocked else None,
        "message": blocked[1] if blocked else None,
        "review": mine(user, product_id),
    }


# --- Shopper actions -----------------------------------------------------------


@transaction.atomic
def submit(user: User, product_id: int, *, stars: int, body: str) -> Review:
    """Create or edit the Shopper's review. Stars alone publish at once; new or
    changed text waits for a moderator; unchanged text keeps its state."""
    _published_product(product_id)
    blocked = blocked_reason(user, product_id)
    if blocked:
        raise ForbiddenError(blocked[1], code=blocked[0])
    text = clean_body(body)
    review = (
        Review.objects.select_for_update()
        .filter(user=user, product_id=product_id)
        .first()
    )
    if review is None:
        return Review.objects.create(
            user=user,
            product_id=product_id,
            stars=stars,
            body=text,
            status=ReviewStatus.PENDING if text else ReviewStatus.PUBLISHED,
        )
    review.stars = stars
    previous = review.status
    if not text:
        review.status = ReviewStatus.PUBLISHED
    elif text != review.body:
        review.status = ReviewStatus.PENDING
    if review.status != previous:
        review.moderated_by = None
        review.moderated_at = None
    review.body = text
    review.save()
    return review


def remove(user: User, product_id: int) -> None:
    Review.objects.filter(user=user, product_id=product_id).delete()


# --- reading -------------------------------------------------------------------


def _published() -> QuerySet[Review]:
    return Review.objects.filter(status=ReviewStatus.PUBLISHED)


def ratings_for(product_ids: list[int]) -> dict[int, tuple[float | None, int]]:
    """Per Product: `(average rounded to one decimal, count)` of published reviews."""
    out: dict[int, tuple[float | None, int]] = dict.fromkeys(product_ids, (None, 0))
    rows = (
        _published()
        .filter(product_id__in=product_ids)
        .values("product_id")
        .annotate(avg=Avg("stars"), n=Count("pk"))
    )
    for row in rows:
        out[row["product_id"]] = (round(float(row["avg"]), 1), row["n"])
    return out


def page_for(slug: str, page: int) -> dict:
    profile = (
        ProductProfile.objects.filter(slug=slug, status=ProductStatus.PUBLISHED)
        .select_related("product")
        .first()
    )
    if profile is None:
        raise NotFoundError
    product_id = profile.product_id
    average, count = ratings_for([product_id])[product_id]
    offset = (page - 1) * PAGE_SIZE
    rows = list(
        _published()
        .filter(product_id=product_id)
        .select_related("user")[offset : offset + PAGE_SIZE],
    )
    return {
        "average": average,
        "count": count,
        "page": page,
        "page_size": PAGE_SIZE,
        "items": [
            {
                "id": r.pk,
                "stars": r.stars,
                "body": r.body,
                "author": display_name(r.user),
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


# --- moderation ----------------------------------------------------------------


def queue(status: str | None) -> QuerySet[Review]:
    """Reviews with text for the Dashboard, oldest first for the waiting ones."""
    qs = Review.objects.select_related("user", "product", "moderated_by").exclude(
        body="",
    )
    if status:
        if status not in ReviewStatus.values:
            msg = "Geçersiz durum."
            raise UnprocessableError(msg, fields={"status": [msg]})
        qs = qs.filter(status=status)
    return qs.order_by(
        "created_at" if status == ReviewStatus.PENDING else "-created_at",
    )


def _decide(actor: User, review_id: int, status: ReviewStatus, action: audit.Action):
    with transaction.atomic():
        review = Review.objects.select_for_update().filter(pk=review_id).first()
        if review is None:
            raise NotFoundError
        before = review.status
        review.status = status
        review.moderated_by = actor
        review.moderated_at = timezone.now()
        review.save()
        audit.record(
            actor,
            action,
            target=review,
            summary=f"{review.product.title}: {before} -> {status}",
            before={"status": before},
            after={"status": status},
        )
    return review


def approve(actor: User, review_id: int) -> Review:
    return _decide(
        actor,
        review_id,
        ReviewStatus.PUBLISHED,
        audit.Action.REVIEW_APPROVED,
    )


def reject(actor: User, review_id: int) -> Review:
    return _decide(
        actor,
        review_id,
        ReviewStatus.REJECTED,
        audit.Action.REVIEW_REJECTED,
    )


@transaction.atomic
def delete(actor: User, review_id: int) -> None:
    review = (
        Review.objects.select_for_update()
        .select_related("product")
        .filter(pk=review_id)
        .first()
    )
    if review is None:
        raise NotFoundError
    audit.record(
        actor,
        audit.Action.REVIEW_DELETED,
        target=review,
        summary=f"{review.product.title}: {review.stars} yıldız",
        before={"status": review.status, "stars": review.stars, "body": review.body},
    )
    review.delete()
