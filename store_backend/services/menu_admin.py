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
from django.db.models import F
from django.db.models import Func
from django.db.models import Value
from django.db.models.functions import Lower
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import CampaignPage
from store_backend.catalog.models import Collection
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import campaign_pages
from store_backend.services import categories
from store_backend.services import collections
from store_backend.services import legal_pages
from store_backend.services.menus import DEPTH_LIMIT
from store_backend.services.menus import brand_is_public
from store_backend.services.menus import product_page
from store_backend.services.search import fold
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


def _product_target(item: MenuItem) -> tuple[str, str | None, Reason]:
    product = Product.objects.select_related("profile").get(pk=item.product_id)
    page = product_page(product)
    if page is None:
        return "", None, "target_missing"
    public = page.status == ProductStatus.PUBLISHED
    return (
        product.title,
        f"/urun/{page.slug}",
        "" if public else "target_not_public",
    )


def _brand_target(item: MenuItem) -> tuple[str, str | None, Reason]:
    brand = Brand.objects.get(pk=item.brand_id)
    reason = "" if brand_is_public(brand.pk) else "target_not_public"
    return brand.name, f"/ara?brand={brand.pk}", reason


def _collection_target(item: MenuItem) -> tuple[str, str | None, Reason]:
    collection = Collection.objects.get(pk=item.collection_id)
    reason = "" if collections.is_public(collection) else "target_not_public"
    return collection.name, collections.path(collection), reason


def _campaign_page_target(item: MenuItem) -> tuple[str, str | None, Reason]:
    page = CampaignPage.objects.get(pk=item.campaign_page_id)
    reason = "" if campaign_pages.is_public(page) else "target_not_public"
    return page.title, campaign_pages.path(page), reason


def _target(item: MenuItem) -> tuple[str, str | None, Reason]:  # noqa: C901, PLR0911
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
    if item.target_type == kind.PRODUCT:
        return _product_target(item)
    if item.target_type == kind.BRAND:
        return _brand_target(item)
    if item.target_type == kind.COLLECTION:
        return _collection_target(item)
    if item.target_type == kind.CAMPAIGN_PAGE:
        return _campaign_page_target(item)
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
        "product_id": item.product_id,
        "brand_id": item.brand_id,
        "collection_id": item.collection_id,
        "campaign_page_id": item.campaign_page_id,
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


_REFERENCES = (
    (MenuItem.Target.CATEGORY, "category_id", "Kategori bulunamadı."),
    (MenuItem.Target.PRODUCT, "product_id", "Ürün bulunamadı."),
    (MenuItem.Target.BRAND, "brand_id", "Marka bulunamadı."),
    (MenuItem.Target.COLLECTION, "collection_id", "Koleksiyon bulunamadı."),
    (MenuItem.Target.CAMPAIGN_PAGE, "campaign_page_id", "Kampanya sayfası bulunamadı."),
)


def _check_known(kind: str, node: dict, path: str, known: dict[str, set[int]]) -> None:
    for target, field, message in _REFERENCES:
        if kind == target and node[field] not in known[field]:
            raise _reject(message, f"{path}.{field}", code="menu_unknown_target")


def _check_target(node: dict, path: str, known: dict[str, set[int]]) -> None:
    kind = node["target_type"]
    filled = {
        "category_id": node["category_id"] is not None,
        "product_id": node["product_id"] is not None,
        "brand_id": node["brand_id"] is not None,
        "collection_id": node["collection_id"] is not None,
        "campaign_page_id": node["campaign_page_id"] is not None,
        "legal_page": bool(node["legal_page"]),
        "builtin": bool(node["builtin"]),
        # An empty URL on a URL item is a bad URL, not a missing target.
        "url": node["url"] is not None
        if kind == MenuItem.Target.URL
        else bool(node["url"]),
    }
    owns = {
        MenuItem.Target.CATEGORY: "category_id",
        MenuItem.Target.PRODUCT: "product_id",
        MenuItem.Target.BRAND: "brand_id",
        MenuItem.Target.COLLECTION: "collection_id",
        MenuItem.Target.CAMPAIGN_PAGE: "campaign_page_id",
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
    _check_known(kind, node, path, known)
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


def _known_targets(nodes: list[dict]) -> dict[str, set[int]]:
    """Which of the referenced Categories, Products, Brands and Collections exist."""
    # Only a Product with a page of its own can be linked: not a variant.
    models = {
        "category_id": Category.objects,
        "product_id": Product.objects.filter(profile__isnull=False),
        "brand_id": Brand.objects,
        "collection_id": Collection.objects,
        "campaign_page_id": CampaignPage.objects,
    }
    return {
        field: set(
            rows.filter(
                pk__in={n[field] for n in nodes if n[field] is not None},
            ).values_list("pk", flat=True),
        )
        for field, rows in models.items()
    }


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
    known = _known_targets([n for n, _, _ in flat])
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
        "product_id",
        "brand_id",
        "collection_id",
        "campaign_page_id",
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
            product_id=node["product_id"],
            brand_id=node["brand_id"],
            collection_id=node["collection_id"],
            campaign_page_id=node["campaign_page_id"],
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


# --- the target picker --------------------------------------------------------------

PICK_LIMIT = 10


# `search.fold` as SQL: Turkish letters to plain ones first (Postgres lowercases "İ"
# to a two-codepoint string outside a Turkish locale), then lowercase.
_FROM, _TO = "İIıŞşĞğÜüÖöÇçÂâÎîÛû", "iiissgguuooccaaiiuu"


def _folded(column: str) -> Lower:
    return Lower(Func(F(column), Value(_FROM), Value(_TO), function="translate"))


def _match(target_type: str, label: str, **reference: Any) -> dict[str, Any]:
    return {
        "category_id": None,
        "product_id": None,
        "brand_id": None,
        "collection_id": None,
        "campaign_page_id": None,
        "legal_page": None,
        "target_type": target_type,
        "target_label": label,
        **reference,
    }


def search_targets(q: str) -> list[dict[str, Any]]:
    """Categories, Legal pages, Products, Brands and Collections whose name matches
    `q`, shaped like the item fields so the builder can drop a result into the tree as
    it is. Every match is returned, public or not: the builder's read flags the
    latter."""
    folded = fold(q)
    if not folded:
        return []
    found = [
        _match("category", c.name, category_id=c.pk)
        for c in Category.objects.alias(f=_folded("name")).filter(f__contains=folded)[
            :PICK_LIMIT
        ]
    ]
    found.extend(
        _match("legal_page", v.title, legal_page=v.page)
        for v in legal_pages.current_all()
        if folded in fold(v.title)
    )
    found.extend(
        _match("product", p.title, product_id=p.pk)
        for p in Product.objects.alias(f=_folded("title")).filter(
            f__contains=folded,
            parent__isnull=True,
        )[:PICK_LIMIT]
    )
    found.extend(
        _match("brand", b.name, brand_id=b.pk)
        for b in Brand.objects.alias(f=_folded("name")).filter(f__contains=folded)[
            :PICK_LIMIT
        ]
    )
    found.extend(
        _match("collection", c.name, collection_id=c.pk)
        for c in Collection.objects.alias(f=_folded("name")).filter(
            f__contains=folded,
        )[:PICK_LIMIT]
    )
    found.extend(
        _match("campaign_page", p.title, campaign_page_id=p.pk)
        for p in CampaignPage.objects.alias(f=_folded("title")).filter(
            f__contains=folded,
        )[:PICK_LIMIT]
    )
    return found
