"""Contact categories: the topics on the contact form. Owner only."""

from __future__ import annotations

from http import HTTPStatus

from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires_owner
from store_backend.services import contact_threads

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
