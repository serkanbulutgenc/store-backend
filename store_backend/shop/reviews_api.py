# ruff: noqa: TC003
"""Reviews on the Storefront: the public list and the Shopper's own review."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from ninja import Field
from ninja import Query
from ninja import Router
from ninja import Schema

from store_backend.api.auth import CustomerAuth
from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import ProductProfile
from store_backend.services import reviews

router = Router(tags=["reviews"], auth=None)


class ReviewOut(Schema):
    id: int
    stars: int
    body: str
    author: str
    created_at: datetime


class ReviewPageOut(Schema):
    average: float | None
    count: int
    page: int
    page_size: int
    items: list[ReviewOut]


class PageQuery(Schema):
    page: int = Field(1, ge=1)


@router.get(
    "/products/{slug}/reviews",
    operation_id="reviews_list",
    response=ReviewPageOut,
)
def list_reviews(request, slug: str, params: Query[PageQuery]):
    """Published reviews, newest first, with the average and count (10 per page)."""
    return reviews.page_for(slug, params.page)


class ReviewIn(Schema):
    stars: int = Field(ge=1, le=5)
    body: str = ""


class MyReviewOut(Schema):
    id: int
    stars: int
    body: str
    status: str


class MyReviewStateOut(Schema):
    can_review: bool
    reason: str | None
    message: str | None
    review: MyReviewOut | None


def _product_id(slug: str) -> int:
    pk = (
        ProductProfile.objects.filter(slug=slug, status="published")
        .values_list("product_id", flat=True)
        .first()
    )
    if pk is None:
        raise NotFoundError
    return pk


@router.get(
    "/products/{slug}/my-review",
    auth=CustomerAuth(),
    operation_id="reviews_get_mine",
    response=MyReviewStateOut,
)
def my_review(request, slug: str):
    """Whether the signed-in Shopper may review this Product (and why not), plus
    their review if they wrote one."""
    return reviews.state_for(request.auth, _product_id(slug))


@router.put(
    "/account/reviews/{product_id}",
    auth=CustomerAuth(),
    operation_id="reviews_save_mine",
    response=MyReviewOut,
)
def save_review(request, product_id: int, data: ReviewIn):
    """Create or edit my review. 403 `review_not_delivered` or `review_fully_returned`
    when not eligible; 422 for HTML or text over 2000 characters. Stars alone publish
    at once; new or changed text waits for a moderator."""
    return reviews.submit(request.auth, product_id, stars=data.stars, body=data.body)


@router.delete(
    "/account/reviews/{product_id}",
    auth=CustomerAuth(),
    operation_id="reviews_delete_mine",
    response={HTTPStatus.NO_CONTENT: None},
)
def delete_review(request, product_id: int):
    reviews.remove(request.auth, product_id)
    return HTTPStatus.NO_CONTENT, None
