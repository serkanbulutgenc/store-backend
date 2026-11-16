"""Menus: the Header and Footer menus as the Storefront shows them (docs/adr/0007).

An item points at its target by reference, so what is returned here is resolved on
every read: label and link follow the target when it is renamed or its slug changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from store_backend.services import browse
from store_backend.services import categories
from store_backend.services import legal_pages
from store_backend.shop.models import MENU_BUILTIN_PAGES
from store_backend.shop.models import Menu
from store_backend.shop.models import MenuItem

if TYPE_CHECKING:
    from oscar.apps.catalogue.models import Category


# How many levels each Menu nests: the Header three, the Footer two.
DEPTH_LIMIT = {Menu.Key.HEADER: 3, Menu.Key.FOOTER: 2}


def _category_node(category: Category, label: str = "") -> dict:
    return {
        "label": label or category.name,
        "url": f"/kategori/{category.slug}",
        "external": False,
        "image_url": browse._thumbnail_url(category.profile),  # noqa: SLF001
        "children": [],
    }


def _subcategories(parent: Category, levels: int) -> list[dict]:
    """The visible descendants of `parent`, `levels` deep, in sibling order."""
    if levels < 1:
        return []
    kids = categories.ordered(
        categories.visible().filter(
            path__startswith=parent.path,
            depth=parent.depth + 1,
        ),
    )
    return [
        {**_category_node(k), "children": _subcategories(k, levels - 1)} for k in kids
    ]


def _leaf(item: MenuItem) -> dict | None:
    """The item itself, without children, or None when its target is not public."""
    if item.target_type == MenuItem.Target.CATEGORY:
        category = categories.visible().filter(pk=item.category_id).first()
        return None if category is None else _category_node(category, item.label)
    if item.target_type == MenuItem.Target.LEGAL_PAGE:
        label = legal_pages.current(item.legal_page).title
        url: str | None = f"/yasal/{legal_pages.slug_of(item.legal_page)}"
    elif item.target_type == MenuItem.Target.BUILTIN:
        label, url = MENU_BUILTIN_PAGES[item.builtin]
    elif item.target_type == MenuItem.Target.URL:
        label, url = "", item.url
    else:  # a group heading links nowhere
        label, url = "", None
    return {
        "label": item.label or label,
        "url": url,
        "external": bool(url) and url.startswith("https://"),
        "image_url": None,
        "children": [],
    }


def _resolve(
    item: MenuItem,
    by_parent: dict[int | None, list[MenuItem]],
    levels_left: int,
) -> dict | None:
    """One item as the Storefront shows it, or None when it is left out. A hidden
    item takes its children with it. `levels_left` counts this item's own level and
    those below it."""
    if item.is_hidden:
        return None
    node = _leaf(item)
    if node is None:
        return None
    if levels_left > 1:
        if item.target_type == MenuItem.Target.CATEGORY and item.include_subcategories:
            node["children"] = _subcategories(item.category, levels_left - 1)
        for child in by_parent.get(item.pk, []):
            resolved = _resolve(child, by_parent, levels_left - 1)
            if resolved is not None:
                node["children"].append(resolved)
    return node


def public() -> dict[str, list[dict]]:
    """Both Menus as resolved trees, keyed by Menu key."""
    result: dict[str, list[dict]] = {key: [] for key in Menu.Key.values}
    for menu in Menu.objects.all():
        by_parent: dict[int | None, list[MenuItem]] = {}
        for item in menu.items.select_related("category"):
            by_parent.setdefault(item.parent_id, []).append(item)
        for item in by_parent.get(None, []):
            node = _resolve(item, by_parent, DEPTH_LIMIT[menu.key])
            if node is not None:
                result[menu.key].append(node)
    return result
