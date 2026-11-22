"""Two response schemas with one name overwrite each other in the OpenAPI spec.

Ninja builds each operation's schema on its own and merges them by class name, so a
second `CampaignRowOut` silently replaces the first in `openapi-*.json`: the API
still answers correctly, but the spec and the frontend's generated types are wrong.
This walks the `Schema` classes each API's routers define and refuses a name used
twice within one API (the Dashboard and the storefront have separate specs).
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from ninja import Schema

from store_backend.api.admin import admin_api
from store_backend.api.store import store_api

# Defined twice with identical fields, so the overwrite changes nothing. Do not add to
# this list without checking the two definitions agree.
IDENTICAL_DUPLICATES = {"ImageDerivativeOut"}


def _subclasses(cls: type):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def _router_modules(api) -> set[str]:
    return {
        operation.view_func.__module__
        for _prefix, router in api._routers  # noqa: SLF001
        for view in router.path_operations.values()
        for operation in view.operations
    }


def _clashes(api) -> dict[str, list[str]]:
    modules = _router_modules(api)
    seen: dict[str, set[str]] = defaultdict(set)
    for cls in _subclasses(Schema):
        if cls.__module__ in modules:
            seen[cls.__name__].add(f"{cls.__module__}.{cls.__qualname__}")
    return {
        name: sorted(where)
        for name, where in seen.items()
        if len(where) > 1 and name not in IDENTICAL_DUPLICATES
    }


@pytest.mark.parametrize("api", [admin_api, store_api], ids=["admin", "store"])
def test_no_two_schemas_of_one_api_share_a_name(api):
    api.get_openapi_schema()  # imports every router, so every Schema exists
    assert _clashes(api) == {}
