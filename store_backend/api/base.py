from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI

from store_backend.api.errors import install_error_handlers

if TYPE_CHECKING:
    from ninja.operation import Operation


class StrictNinjaAPI(NinjaAPI):
    """Every operation must name its `operation_id`, e.g. `catalog_list_products`."""

    def get_openapi_operation_id(self, operation: Operation) -> str:
        msg = (
            f"{operation.view_func.__module__}.{operation.view_func.__name__} "
            "has no explicit operation_id"
        )
        raise ValueError(msg)


def create_api(*, title: str, urls_namespace: str, **kwargs) -> StrictNinjaAPI:
    api = StrictNinjaAPI(
        title=title,
        version="1",
        urls_namespace=urls_namespace,
        docs_decorator=staff_member_required,
        **kwargs,
    )
    install_error_handlers(api)
    return api
