"""The Homepage layout (GLOSSARY: Homepage layout, Homepage section; ADR 0012).

The layout is one document: an ordered list of Homepage sections. Each section has a
type fixed in code and that type's own settings. `public` returns the layout with every
section already resolved for the Storefront; a section that has nothing to show, or
whose target is gone, is left out rather than failing the page.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from typing import Any
from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic import model_validator

from store_backend.api.errors import ConflictError
from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import HomepageLayout
from store_backend.services import approval
from store_backend.services import audit
from store_backend.services import banners
from store_backend.services import browse
from store_backend.services import categories
from store_backend.services import collections
from store_backend.staff.models import ApprovalStatus
from store_backend.staff.permissions import Perm

MAX_SECTIONS = 20
MAX_ITEMS = 24
MIN_ITEMS = 4
DEFAULT_WINDOW = 30
DEFAULT_ITEMS = 12
SLIDER_ITEMS = 5
#: The Collection feeding the default slider (GLOSSARY: Home slider Collection).
SLIDER_COLLECTION_SLUG = "ana-sayfa"

Display = Literal["grid", "carousel", "list"]
Count = Annotated[int, Field(ge=MIN_ITEMS, le=MAX_ITEMS)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Section(_Base):
    id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=120)
    link: str = Field(default="", max_length=300)
    visible: bool = True
    display: Display = "carousel"


class SliderSettings(_Base):
    #: `home`: the `ana-sayfa` Collection's Products, else the newest Products (today's
    #: page). `collection`: the Products of the Collection `collection_id`. `own`: the
    #: slides uploaded in the Vitrin (`services/homepage_images.py`), in `banner_ids`
    #: order.
    source: Literal["home", "collection", "own"] = "home"
    count: Annotated[int, Field(ge=1, le=MAX_ITEMS)] = SLIDER_ITEMS
    banner_ids: list[int] = Field(default_factory=list, max_length=MAX_ITEMS)
    collection_id: int | None = None

    @model_validator(mode="after")
    def _needs_its_pick(self) -> SliderSettings:
        if self.source == "own" and not self.banner_ids:
            message = "Upload at least one slide."
            raise ValueError(message)
        if self.source == "collection" and self.collection_id is None:
            message = "Choose a collection."
            raise ValueError(message)
        if len(set(self.banner_ids)) != len(self.banner_ids):
            message = "A banner can be picked once."
            raise ValueError(message)
        return self


class SliderSection(_Section):
    type: Literal["slider"]
    settings: SliderSettings = SliderSettings()


class ProductsSettings(_Base):
    source: Literal[
        "latest",
        "best_selling",
        "on_sale",
        "category",
        "hand_picked",
        "recently_viewed",
    ] = "latest"
    count: Count = DEFAULT_ITEMS
    #: Best-selling: units sold over the last `window` days (never view tracking).
    window: Literal[7, 30, 90] = DEFAULT_WINDOW
    #: Category source: the Category (and its visible sub-categories) by id.
    category_id: int | None = None
    #: Hand-picked source: Product ids, in the Owner's order.
    product_ids: list[int] = Field(default_factory=list, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _needs_its_pick(self) -> ProductsSettings:
        if self.source == "category" and self.category_id is None:
            message = "Choose a category."
            raise ValueError(message)
        if self.source == "hand_picked" and not self.product_ids:
            message = "Pick at least one product."
            raise ValueError(message)
        if len(set(self.product_ids)) != len(self.product_ids):
            message = "A product can be picked once."
            raise ValueError(message)
        return self


class ProductsSection(_Section):
    type: Literal["products"]
    settings: ProductsSettings = ProductsSettings()


class CategoriesSettings(_Base):
    #: `home`: the Categories flagged `show_on_home`; `picked`: `category_ids`, in
    #: the Owner's order.
    source: Literal["home", "picked"] = "home"
    count: Count = DEFAULT_ITEMS
    category_ids: list[int] = Field(default_factory=list, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _needs_its_pick(self) -> CategoriesSettings:
        if self.source == "picked" and not self.category_ids:
            message = "Pick at least one category."
            raise ValueError(message)
        if len(set(self.category_ids)) != len(self.category_ids):
            message = "A category can be picked once."
            raise ValueError(message)
        return self


class CategoriesSection(_Section):
    type: Literal["categories"]
    settings: CategoriesSettings = CategoriesSettings()


class BannerSettings(_Base):
    #: The section image (`services/homepage_images.py`) this strip shows, when it is
    #: live. With none the section has nothing to show.
    banner_id: int | None = None


class BannerSection(_Section):
    type: Literal["banner"]
    settings: BannerSettings = BannerSettings()


class RichTextSettings(_Base):
    body: str = Field(default="", max_length=20_000)


class RichTextSection(_Section):
    type: Literal["rich_text"]
    settings: RichTextSettings = RichTextSettings()


Section = Annotated[
    SliderSection
    | ProductsSection
    | CategoriesSection
    | BannerSection
    | RichTextSection,
    Field(discriminator="type"),
]
_SECTIONS = TypeAdapter(list[Section])


def default_sections() -> list[dict[str, Any]]:
    """Today's home page, as it was hardcoded before the layout existed."""
    return [
        {"id": "hero", "type": "slider"},
        {
            "id": "recently-viewed",
            "type": "products",
            "title": "Son baktıklarınız",
            "settings": {"source": "recently_viewed"},
        },
        {
            "id": "latest",
            "type": "products",
            "title": "Yeni gelenler",
            "link": "/ara?sort=newest",
            "settings": {"source": "latest"},
        },
        {
            "id": "on-sale",
            "type": "products",
            "title": "İndirimdekiler",
            "link": "/ara?on_sale=true",
            "settings": {"source": "on_sale"},
        },
        {
            "id": "best-selling",
            "type": "products",
            "title": "Çok satanlar",
            "link": "/ara?sort=popular",
            "settings": {"source": "best_selling"},
        },
        {"id": "categories", "type": "categories", "title": "Kategoriler"},
    ]


def _refuse(message: str) -> UnprocessableError:
    return UnprocessableError(message)


def validate(raw: object) -> list[Section]:
    """The sections of a layout document, checked: known types, valid settings, unique
    ids, at most `MAX_SECTIONS`."""
    try:
        sections = _SECTIONS.validate_python(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = f"Invalid homepage layout at {where}: {first['msg']}"
        raise _refuse(message) from error
    if len(sections) > MAX_SECTIONS:
        message = f"A homepage layout has at most {MAX_SECTIONS} sections."
        raise _refuse(message)
    ids = [s.id for s in sections]
    if len(set(ids)) != len(ids):
        message = "Homepage section ids must be unique."
        raise _refuse(message)
    return sections


def get() -> HomepageLayout:
    """The layout row; created with the default layout if it is missing."""
    layout, created = HomepageLayout.objects.get_or_create(
        pk=1,
        defaults={"sections": default_sections(), "status": ApprovalStatus.PUBLISHED},
    )
    if created:
        approval.keep_version(KIND, layout, None)
    return layout


# --- resolving ---------------------------------------------------------------------


def _products(section: ProductsSection) -> list[dict]:
    settings = section.settings
    match settings.source:
        case "hand_picked":
            return _hand_picked(settings)
        case "category":
            category = categories.visible().filter(pk=settings.category_id).first()
            if category is None:
                return []
            filters = browse.Filters(category=category.slug)
            return browse.cards(filters, limit=settings.count)
        case "best_selling":
            since = timezone.now() - timedelta(days=settings.window)
            filters = browse.Filters(sold_since=since)
            return browse.cards(filters, sort="popular", limit=settings.count)
        case _:
            filters = browse.Filters(on_sale=settings.source == "on_sale")
            return browse.cards(filters, limit=settings.count)


def _hand_picked(settings: ProductsSettings) -> list[dict]:
    """The picked Products in the Owner's order; unpublished, deleted and sold-out
    ones are skipped."""
    found = {
        p.product_id: p
        for p in browse.published().filter(
            product_id__in=settings.product_ids,
        )
    }
    ordered = [found[i] for i in settings.product_ids if i in found]
    cards = [c for c in browse.cards_for(ordered) if not c["sold_out"]]
    return cards[: settings.count]


def _slider(section: SliderSection) -> dict[str, Any]:
    settings = section.settings
    count = settings.count
    if settings.source == "own":
        return {"banners": banners.picked(settings.banner_ids)[:count]}
    if settings.source == "collection":
        return {"products": _collection_cards(count, id_=settings.collection_id)}
    featured = _collection_cards(count)
    return {"products": featured or browse.cards(browse.Filters(), limit=count)}


def _collection_cards(limit: int, id_: int | None = None) -> list[dict]:
    """The published Collection's visible Products (the `ana-sayfa` one unless an id
    is given); nothing when it is missing or not published."""
    wanted = {"pk": id_} if id_ is not None else {"slug": SLIDER_COLLECTION_SLUG}
    collection = Collection.objects.filter(
        status=CollectionStatus.PUBLISHED,
        **wanted,
    ).first()
    if collection is None:
        return []
    profiles = collections.visible_products(collection)[:limit]
    return browse.cards_for(profiles)


def _products_data(section: ProductsSection) -> dict[str, Any]:
    if section.settings.source == "recently_viewed":
        # The list lives in the Shopper's browser (GLOSSARY: Recently viewed).
        return {"client_resolved": True}
    return {"products": _products(section)}


def _categories_data(section: CategoriesSection) -> dict[str, Any]:
    settings = section.settings
    if settings.source == "picked":
        return {
            "categories": browse.picked_categories(settings.category_ids)[
                : settings.count
            ],
        }
    return {"categories": browse.home_categories()[: settings.count]}


def _banner_data(section: BannerSection) -> dict[str, Any]:
    banner_id = section.settings.banner_id
    return {"banners": banners.picked([banner_id]) if banner_id is not None else []}


def _rich_text_data(section: RichTextSection) -> dict[str, Any]:
    body = section.settings.body
    return {"body": body if body.strip() else None}


def _slider_data(section: SliderSection) -> dict[str, Any]:
    return _slider(section)


_DATA = {
    "slider": _slider_data,
    "products": _products_data,
    "categories": _categories_data,
    "banner": _banner_data,
    "rich_text": _rich_text_data,
}


def _resolve(section: Section) -> dict[str, Any] | None:
    """The Storefront's view of one section, or None when it has nothing to show."""
    data = _DATA[section.type](section)
    if not any(data.values()):
        return None
    return {
        "id": section.id,
        "type": section.type,
        "title": section.title,
        "link": section.link,
        "display": section.display,
        "source": getattr(section.settings, "source", None),
        **data,
    }


#: Why a visible section can come up empty, by (type, source). The Owner's words.
_EMPTY_BECAUSE = {
    ("slider", "own"): "Yüklenen slaytların hiçbiri gösterilemiyor.",
    ("slider", "collection"): (
        "Koleksiyon silinmiş, yayında değil ya da gösterilecek ürünü yok."
    ),
    ("products", "hand_picked"): "Seçilen ürünlerin hiçbiri satışta değil.",
    ("products", "category"): (
        "Kategori silinmiş, gizli ya da gösterilecek ürünü yok."
    ),
    ("categories", "picked"): "Seçilen kategorilerin hiçbiri görünür değil.",
    ("categories", "home"): "Ana sayfada gösterilen kategori yok.",
    ("banner", "picked"): "Seçilen afiş yayında değil.",
    ("banner", "slot"): "Bu bölüme henüz görsel yüklenmedi.",
    ("rich_text", None): "Metin boş.",
}


def _why_empty(section: Section) -> str:
    """Why a visible section has nothing to show (store-infra#163)."""
    source = getattr(section.settings, "source", None)
    if section.type == "banner":
        source = "picked" if section.settings.banner_id is not None else "slot"
    return _EMPTY_BECAUSE.get((section.type, source), "Gösterilecek ürün yok.")


def warnings(sections: list[Section]) -> dict[str, str]:
    """For each visible section the Storefront would skip because it has nothing to
    show: why. Hidden sections are left out; the Owner hid them on purpose."""
    return {s.id: _why_empty(s) for s in sections if s.visible and _resolve(s) is None}


def public() -> dict[str, list[dict[str, Any]]]:
    """The layout as the Storefront draws it: visible sections in order, resolved."""
    return _drawn(validate(get().sections))


def preview() -> dict[str, list[dict[str, Any]]]:
    """The layout a Dashboard user is looking at, drawn the way the Storefront would
    draw it (store-infra#161): the pending change when one is waiting for the Owner,
    else the live layout. Unsaved editor edits are not included."""
    layout = get()
    change = approval.pending_change(KIND, layout.pk)
    raw = change.payload["sections"] if change is not None else layout.sections
    return _drawn(validate(raw))


def _drawn(sections: list[Section]) -> dict[str, list[dict[str, Any]]]:
    resolved = (_resolve(s) for s in sections if s.visible)
    return {"sections": [r for r in resolved if r is not None]}


# --- editing -----------------------------------------------------------------------
#
# The layout is under the Approval rule (ADR 0008, 0012). It is always live, so a Staff
# edit is never a draft: it is the layout's one pending change, which the Owner approves
# or rejects in the approval queue. The Owner saves live, unless a change is waiting.


def _clean(changes: dict[str, Any], _item: Any) -> dict[str, Any]:
    """The sections of a change, checked and written out in full."""
    if "sections" not in changes:
        return {}
    return {"sections": [s.model_dump() for s in validate(changes["sections"])]}


KIND = approval.register(
    approval.Kind(
        key="homepage",
        label="Vitrin",
        model=HomepageLayout,
        manage_perm=Perm.MANAGE_HOMEPAGE,
        edit_perm=Perm.EDIT_HOMEPAGE,
        fields=("sections",),
        title=lambda _item: "Vitrin düzeni",
        clean=_clean,
    ),
)


def for_editing() -> dict[str, Any]:
    """The live layout as the Dashboard edits it (every section, hidden ones too, with
    its settings, unresolved), plus the waiting change, if any, and why the last one
    was rejected."""
    layout = get()
    change = approval.pending_change(KIND, layout.pk)
    pending = None
    if change is not None:
        submitter = change.submitted_by
        pending_sections = validate(change.payload["sections"])
        pending = {
            "sections": pending_sections,
            "warnings": warnings(pending_sections),
            "submitted_by": (submitter.name or submitter.email) if submitter else None,
            "submitted_at": change.updated_at,
        }
    live = validate(layout.sections)
    return {
        "sections": live,
        "warnings": warnings(live),
        "pending": pending,
        "rejection_reason": layout.rejection_reason,
    }


def _normalised(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored sections with defaults filled in, for the audit trail. A document
    that no longer validates is recorded as it is, so it can still be replaced."""
    try:
        return [s.model_dump() for s in validate(raw)]
    except UnprocessableError:
        return raw


@transaction.atomic
def save(actor: Any, raw_sections: list[dict[str, Any]]) -> dict[str, Any]:
    """Save the whole layout. The Owner's save is live at once; a Staff save is held as
    the layout's pending change (a second one amends it)."""
    layout = get()
    if not approval.can_manage(actor, KIND):
        approval.edit(actor, KIND, layout.pk, sections=raw_sections)
        return for_editing()
    sections = validate(raw_sections)
    layout = HomepageLayout.objects.select_for_update().get(pk=1)
    if approval.has_pending_change(KIND, layout.pk):
        message = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
        raise ConflictError(message, code="approval_has_pending_change")
    before = _normalised(layout.sections)
    layout.sections = [s.model_dump() for s in sections]
    layout.rejection_reason = ""
    layout.save()
    approval.keep_version(KIND, layout, actor)
    audit.record(
        actor,
        audit.Action.HOMEPAGE_SAVED,
        target=layout,
        summary="Vitrin düzeni kaydedildi",
        before={"sections": before},
        after={"sections": layout.sections},
    )
    return for_editing()


# --- history -----------------------------------------------------------------------
#
# Every version that went live is kept (`ApprovedVersion`): the first layout, each Owner
# save and each approved Staff change. Restoring one makes it the live layout again as a
# new version, so history only ever grows.


def history() -> list[dict[str, Any]]:
    """The approved versions, newest first, with date, approver and section count."""
    layout = get()
    return [
        {
            "version": v.version,
            "approved_at": v.approved_at,
            "approved_by": (v.approved_by.name or v.approved_by.email)
            if v.approved_by
            else None,
            "section_count": len(v.payload.get("sections", [])),
        }
        for v in approval.versions(KIND, layout.pk)
    ]


@transaction.atomic
def restore(actor: Any, version: int) -> dict[str, Any]:
    """The Owner puts an earlier version back live, as a new version."""
    if not approval.can_manage(actor, KIND):
        raise ForbiddenError
    layout = HomepageLayout.objects.select_for_update().get(pk=get().pk)
    if approval.has_pending_change(KIND, layout.pk):
        message = "Bekleyen bir değişiklik var; önce onaylayın veya reddedin."
        raise ConflictError(message, code="approval_has_pending_change")
    old = next(
        (v for v in approval.versions(KIND, layout.pk) if v.version == version),
        None,
    )
    if old is None:
        raise NotFoundError
    sections = validate(old.payload["sections"])
    before = _normalised(layout.sections)
    layout.sections = [s.model_dump() for s in sections]
    layout.rejection_reason = ""
    layout.save()
    kept = approval.keep_version(KIND, layout, actor)
    audit.record(
        actor,
        audit.Action.HOMEPAGE_RESTORED,
        target=layout,
        summary=f"Vitrin düzeni geri yüklendi: sürüm {version} → {kept.version}",
        before={"sections": before},
        after={"sections": layout.sections},
    )
    return for_editing()
