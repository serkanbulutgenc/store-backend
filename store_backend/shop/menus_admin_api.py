"""The Owner's Menu builder API: read a Menu for editing, save the whole tree."""

from __future__ import annotations

from typing import Literal

from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires
from store_backend.services import menu_admin
from store_backend.staff.permissions import Perm

router = Router(tags=["menus"])

Target = Literal[
    "category",
    "product",
    "brand",
    "legal_page",
    "builtin",
    "url",
    "group",
]


class MenuItemIn(Schema):
    """One item and the items under it. List order is Menu order."""

    target_type: Target
    category_id: int | None = None
    product_id: int | None = None
    brand_id: int | None = None
    include_subcategories: bool = False
    legal_page: str | None = None
    builtin: str | None = None
    url: str | None = None
    label: str = ""
    is_hidden: bool = False
    children: list[MenuItemIn] = []


class MenuIn(Schema):
    items: list[MenuItemIn]


class MenuItemOut(Schema):
    target_type: Target
    category_id: int | None
    product_id: int | None
    brand_id: int | None
    include_subcategories: bool
    legal_page: str | None
    builtin: str | None
    url: str | None
    label: str
    is_hidden: bool
    target_label: str
    link: str | None
    not_shown_reason: (
        Literal["hidden", "parent_not_shown", "target_not_public", "target_missing"]
        | None
    )
    children: list[MenuItemOut]


class MenuOut(Schema):
    key: str
    depth_limit: int
    items: list[MenuItemOut]


@router.get(
    "/menus/{key}",
    auth=requires(Perm.MANAGE_MENUS),
    operation_id="menus_get_for_editing",
    response=MenuOut,
)
def get_menu(request, key: str):
    """The saved tree with hidden and non-public items, each flagged with why it is
    not shown."""
    return menu_admin.for_editing(key)


@router.put(
    "/menus/{key}",
    auth=requires(Perm.MANAGE_MENUS),
    operation_id="menus_save",
    response=MenuOut,
)
def save_menu(request, key: str, data: MenuIn):
    """Replace the whole tree atomically. Live at once; written to the Audit log."""
    return menu_admin.save(request.auth, key, data.model_dump()["items"])


class MenuTargetOut(Schema):
    """A match for the builder's target picker; spread it into an item as it is."""

    target_type: Literal["category", "product", "brand", "legal_page"]
    target_label: str
    category_id: int | None
    product_id: int | None
    brand_id: int | None
    legal_page: str | None


@router.get(
    "/menu-targets",
    auth=requires(Perm.MANAGE_MENUS),
    operation_id="menus_search_targets",
    response=list[MenuTargetOut],
)
def search_menu_targets(request, q: str = ""):
    """Categories, Legal pages, Products and Brands matching `q` by name."""
    return menu_admin.search_targets(q)
