# ruff: noqa: TC003
"""Dashboard review moderation: Owner and Staff (a correctable action, ADR 0001)."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import reviews
from store_backend.staff.permissions import Perm

router = Router(tags=["reviews"])


class AdminReviewOut(Schema):
    id: int
    product_id: int
    product_title: str
    author: str
    author_email: str
    stars: int
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    moderated_by: str | None
    moderated_at: datetime | None

    @staticmethod
    def resolve_product_title(obj) -> str:
        return obj.product.title

    @staticmethod
    def resolve_author(obj) -> str:
        return reviews.display_name(obj.user)

    @staticmethod
    def resolve_author_email(obj) -> str:
        return obj.user.email

    @staticmethod
    def resolve_moderated_by(obj) -> str | None:
        return obj.moderated_by.email if obj.moderated_by else None


@router.get(
    "/reviews",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="reviews_admin_list",
    response=list[AdminReviewOut],
)
@paginate(PageOffsetPagination)
def list_reviews(request, status: str | None = None):
    """Reviews with text, by `status` (pending, published or rejected). Pending ones
    come oldest first."""
    return reviews.queue(status)


@router.post(
    "/reviews/{review_id}/approve",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="reviews_admin_approve",
    response=AdminReviewOut,
)
def approve_review(request, review_id: int):
    return reviews.approve(request.auth, review_id)


@router.post(
    "/reviews/{review_id}/reject",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="reviews_admin_reject",
    response=AdminReviewOut,
)
def reject_review(request, review_id: int):
    return reviews.reject(request.auth, review_id)


@router.delete(
    "/reviews/{review_id}",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="reviews_admin_delete",
    response={HTTPStatus.NO_CONTENT: None},
)
def delete_review(request, review_id: int):
    reviews.delete(request.auth, review_id)
    return HTTPStatus.NO_CONTENT, None
