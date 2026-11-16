"""The Owner's Menu editing: read the raw tree, save the whole tree (docs/adr/0007).

A save replaces every item of the Menu in one transaction, so a rejected or failed save
changes nothing. Items are matched by nothing but their place in the request: the
order of the list is the order of the Menu.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from oscar.apps.catalogue.models import Category

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import categories
from store_backend.services import legal_pages
from store_backend.services.menus import DEPTH_LIMIT
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import MENU_BUILTIN_PAGES
from store_backend.shop.models import Menu
from store_backend.shop.models import MenuItem

if TYPE_CHECKING:
    from collections.abc import Iterator

    from store_backend.users.models import User

LABEL_MAX = 200
URL_MAX = 500
ITEMS_MAX = 300
# The Legal page index is the page the Storefront's "Yasal sayfalarda ara" opens.
LEGAL_INDEX_BUILTIN = "legal_search"
LEGAL_INDEX_PATH = MENU_BUILTIN_PAGES[LEGAL_INDEX_BUILTIN][1]

# An internal path: one leading slash, no whitespace or control characters.
_INTERNAL = re.compile(r"/(?![/\\])[^\s\x00-\x1f\x7f\\]*")
# https://host[:port][/path]: a real host, no credentials, whitespace or controls.
_EXTERNAL = re.compile(
    r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]*)(?::\d{1,5})?(?:[/?#][^\s\x00-\x1f\x7f\\]*)?",
)

Reason = str  # why an item is not shown; "" when it is


def _reject(message: str, path: str, *, code: str) -> UnprocessableError:
    return UnprocessableError(message, code=code, fields={path: [message]})


# --- reading ------------------------------------------------------------------------


def _menu(key: str) -> Menu:
    menu = Menu.objects.filter(key=key).first()
    if menu is None:
        raise NotFoundError
    return menu


def _target(item: MenuItem) -> tuple[str, str | None, Reason]:  # noqa: PLR0911
    """The target's own label, its URL, and why the Storefront would leave the item
    out for the target's sake ("" when the target is public)."""
    kind = MenuItem.Target
    if item.target_type == kind.CATEGORY:
        category = Category.objects.filter(pk=item.category_id).first()
        if category is None:
            return "", None, "target_missing"
        public = categories.visible().filter(pk=category.pk).exists()
        return (
            category.name,
            f"/kategori/{category.slug}",
            "" if public else ("target_not_public"),
        )
    if item.target_type == kind.LEGAL_PAGE:
        slug = (
            legal_pages.slug_of(item.legal_page)
            if item.legal_page in LEGAL_PAGES
            else None
        )
        try:
            title = legal_pages.current(item.legal_page).title
        except NotFoundError:
            return "", None, "target_missing"
        return title, f"/yasal/{slug}", ""
    if item.target_type == kind.BUILTIN:
        if item.builtin not in MENU_BUILTIN_PAGES:
            return "", None, "target_missing"
        label, url = MENU_BUILTIN_PAGES[item.builtin]
        return label, url, ""
    if item.target_type == kind.URL:
        return "", item.url, ""
    return "", None, ""  # a group heading


def _node(
    item: MenuItem,
    by_parent: dict[int | None, list[MenuItem]],
    parent_reason: Reason,
) -> dict[str, Any]:
    target_label, url, target_reason = _target(item)
    if item.is_hidden:
        reason = "hidden"
    elif parent_reason:
        reason = "parent_not_shown"
    else:
        reason = target_reason
    return {
        "target_type": item.target_type,
        "category_id": item.category_id,
        "include_subcategories": item.include_subcategories,
        "legal_page": item.legal_page or None,
        "builtin": item.builtin or None,
        "url": item.url or None,
        "label": item.label,
        "is_hidden": item.is_hidden,
        "target_label": target_label,
        "link": url,
        "not_shown_reason": reason or None,
        "children": [
            _node(child, by_parent, reason) for child in by_parent.get(item.pk, [])
        ],
    }


def for_editing(key: str) -> dict[str, Any]:
    """The Menu as saved, hidden and non-public items included, each with the reason
    the Storefront leaves it out (None when it shows)."""
    menu = _menu(key)
    by_parent: dict[int | None, list[MenuItem]] = {}
    for item in menu.items.all():
        by_parent.setdefault(item.parent_id, []).append(item)
    return {
        "key": menu.key,
        "depth_limit": DEPTH_LIMIT[menu.key],
        "items": [_node(i, by_parent, "") for i in by_parent.get(None, [])],
    }


# --- validating ---------------------------------------------------------------------


def _walk(nodes: list[dict], path: str = "items", depth: int = 1) -> Iterator[tuple]:
    for index, node in enumerate(nodes):
        here = f"{path}[{index}]"
        yield node, here, depth
        yield from _walk(node["children"], f"{here}.children", depth + 1)


def check_url(url: str, path: str) -> str:
    """A custom URL: an internal path starting with `/`, or an `https://` address."""
    cleaned = url.strip()
    if len(cleaned) > URL_MAX:
        msg = f"Bağlantı en fazla {URL_MAX} karakter olabilir."
        raise _reject(msg, path, code="menu_url_invalid")
    if not (_INTERNAL.fullmatch(cleaned) or _EXTERNAL.fullmatch(cleaned)):
        msg = "Bağlantı '/' ile başlayan bir iç yol ya da 'https://' adresi olmalı."
        raise _reject(msg, path, code="menu_url_invalid")
    return cleaned


def _check_target(node: dict, path: str, known_categories: set[int]) -> None:
    kind = node["target_type"]
    filled = {
        "category_id": node["category_id"] is not None,
        "legal_page": bool(node["legal_page"]),
        "builtin": bool(node["builtin"]),
        # An empty URL on a URL item is a bad URL, not a missing target.
        "url": node["url"] is not None
        if kind == MenuItem.Target.URL
        else bool(node["url"]),
    }
    owns = {
        MenuItem.Target.CATEGORY: "category_id",
        MenuItem.Target.LEGAL_PAGE: "legal_page",
        MenuItem.Target.BUILTIN: "builtin",
        MenuItem.Target.URL: "url",
    }.get(kind)
    if kind == MenuItem.Target.GROUP:
        if any(filled.values()):
            msg = "Grup başlığının hedefi olamaz."
            raise _reject(msg, path, code="menu_group_has_target")
        if not node["label"].strip():
            msg = "Grup başlığının adı gerekli."
            raise _reject(msg, f"{path}.label", code="menu_group_needs_label")
        return
    if not filled[owns] or any(v for k, v in filled.items() if k != owns):
        msg = "Öğenin tam olarak bir hedefi olmalı ve türüyle uyuşmalı."
        raise _reject(msg, path, code="menu_target_mismatch")
    if node["include_subcategories"] and kind != MenuItem.Target.CATEGORY:
        msg = "Alt kategoriler yalnızca kategori öğelerine eklenebilir."
        raise _reject(msg, f"{path}.include_subcategories", code="menu_target_mismatch")
    if kind == MenuItem.Target.CATEGORY and node["category_id"] not in known_categories:
        msg = "Kategori bulunamadı."
        raise _reject(msg, f"{path}.category_id", code="menu_unknown_target")
    if kind == MenuItem.Target.LEGAL_PAGE and node["legal_page"] not in LEGAL_PAGES:
        msg = "Yasal sayfa bulunamadı."
        raise _reject(msg, f"{path}.legal_page", code="menu_unknown_target")
    if kind == MenuItem.Target.BUILTIN and node["builtin"] not in MENU_BUILTIN_PAGES:
        msg = "Hazır sayfa bulunamadı."
        raise _reject(msg, f"{path}.builtin", code="menu_unknown_target")
    if kind == MenuItem.Target.URL:
        node["url"] = check_url(node["url"], f"{path}.url")


def _has_legal_index(nodes: list[dict]) -> bool:
    """A visible link to the Legal page index: not hidden itself, nor under a hidden
    group, since the Storefront leaves those out."""
    for node in nodes:
        if node["is_hidden"]:
            continue
        pointing = (
            node["target_type"] == MenuItem.Target.BUILTIN
            and node["builtin"] == LEGAL_INDEX_BUILTIN
        ) or (
            node["target_type"] == MenuItem.Target.URL
            and node["url"] == LEGAL_INDEX_PATH
        )
        if pointing or _has_legal_index(node["children"]):
            return True
    return False


def validate(key: str, nodes: list[dict]) -> None:
    """Raise a 422 naming the first broken rule; `nodes` is cleaned in place."""
    limit = DEPTH_LIMIT[key]
    flat = list(_walk(nodes))
    if len(flat) > ITEMS_MAX:
        msg = f"Bir menüde en fazla {ITEMS_MAX} öğe olabilir."
        raise _reject(msg, "items", code="menu_too_large")
    if key == Menu.Key.HEADER and not nodes:
        msg = "Üst menü boş kaydedilemez."
        raise _reject(msg, "items", code="menu_header_empty")
    ids = {
        n["category_id"]
        for n, _, _ in flat
        if n["target_type"] == MenuItem.Target.CATEGORY and n["category_id"] is not None
    }
    known = set(Category.objects.filter(pk__in=ids).values_list("pk", flat=True))
    for node, path, depth in flat:
        if depth > limit:
            msg = f"Bu menü en fazla {limit} seviye olabilir."
            raise _reject(msg, path, code="menu_too_deep")
        if len(node["label"]) > LABEL_MAX:
            msg = f"Ad en fazla {LABEL_MAX} karakter olabilir."
            raise _reject(msg, f"{path}.label", code="menu_label_too_long")
        node["label"] = node["label"].strip()
        _check_target(node, path, known)
    if key == Menu.Key.FOOTER and not _has_legal_index(nodes):
        msg = "Alt menüde yasal sayfalar dizinine bir bağlantı bulunmalı."
        raise _reject(msg, "items", code="menu_footer_needs_legal_index")


# --- saving -------------------------------------------------------------------------


def _snapshot(nodes: list[dict]) -> list[dict]:
    """A compact copy of a tree for the Audit log."""
    keep = (
        "target_type",
        "category_id",
        "include_subcategories",
        "legal_page",
        "builtin",
        "url",
        "label",
        "is_hidden",
    )
    return [
        {**{k: n[k] for k in keep}, "children": _snapshot(n["children"])} for n in nodes
    ]


def _create(menu: Menu, nodes: list[dict], parent: MenuItem | None) -> None:
    for position, node in enumerate(nodes):
        created = MenuItem.objects.create(
            menu=menu,
            parent=parent,
            position=position,
            target_type=node["target_type"],
            category_id=node["category_id"],
            include_subcategories=node["include_subcategories"],
            legal_page=node["legal_page"] or "",
            builtin=node["builtin"] or "",
            url=node["url"] or "",
            label=node["label"],
            is_hidden=node["is_hidden"],
        )
        _create(menu, node["children"], created)


@transaction.atomic
def save(actor: User, key: str, nodes: list[dict]) -> dict[str, Any]:
    """Replace the Menu's whole tree and audit the change. Live at once."""
    menu = Menu.objects.select_for_update().filter(key=key).first()
    if menu is None:
        raise NotFoundError
    validate(key, nodes)
    before = _snapshot(_raw(menu))
    menu.items.all().delete()
    _create(menu, nodes, None)
    after = _snapshot(_raw(menu))
    audit.record(
        actor,
        audit.Action.MENU_SAVED,
        target=menu,
        summary=f"Menü kaydedildi: {menu.get_key_display()}",
        before={"items": before},
        after={"items": after},
    )
    return for_editing(key)


def _raw(menu: Menu) -> list[dict]:
    by_parent: dict[int | None, list[MenuItem]] = {}
    for item in menu.items.all():
        by_parent.setdefault(item.parent_id, []).append(item)
    return [_node(i, by_parent, "") for i in by_parent.get(None, [])]
