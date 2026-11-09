from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from ninja import Field
from ninja import Schema
from ninja.pagination import PaginationBase

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100


class PageOffsetPagination(PaginationBase):
    """Offset pagination returning `{items, total, page, page_size}`."""

    class Input(Schema):
        page: int = Field(1, ge=1)
        page_size: int = Field(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    class Output(Schema):
        items: list[Any]
        total: int
        page: int
        page_size: int

    items_attribute = "items"

    def paginate_queryset(
        self,
        queryset: QuerySet,
        pagination: Input,
        request: HttpRequest,
        **params: Any,
    ) -> dict:
        offset = (pagination.page - 1) * pagination.page_size
        return {
            "items": queryset[offset : offset + pagination.page_size],
            "total": self._items_count(queryset),
            "page": pagination.page,
            "page_size": pagination.page_size,
        }
