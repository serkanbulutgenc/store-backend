from __future__ import annotations

from typing import TYPE_CHECKING

from store_backend.api.errors import UnprocessableError

if TYPE_CHECKING:
    from collections.abc import Collection

    from django.db.models import QuerySet


class OrderingError(UnprocessableError):
    """`ordering` named a field outside the endpoint's allowlist."""


def apply_ordering[T: QuerySet](
    queryset: T,
    ordering: str | None,
    *,
    allowed: Collection[str],
) -> T:
    """Order by `field` or `-field`, only for fields in the per-endpoint allowlist."""
    if not ordering:
        return queryset
    field = ordering.removeprefix("-")
    if field not in allowed:
        msg = "Bu alana göre sıralanamaz."
        raise OrderingError(
            msg,
            fields={
                "ordering": [f"İzin verilen alanlar: {', '.join(sorted(allowed))}"],
            },
        )
    return queryset.order_by(ordering)
