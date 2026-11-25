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

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import HomepageLayout
from store_backend.services import audit
from store_backend.services import banners
from store_backend.services import browse
from store_backend.services import categories
from store_backend.services import collections

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
    #: `home`: the Home hero Banners, else the `ana-sayfa` Collection's Products,
    #: else the newest Products. The default layout uses it; later slices add chosen
    #: sources.
    source: Literal["home"] = "home"
    count: Annotated[int, Field(ge=1, le=MAX_ITEMS)] = SLIDER_ITEMS


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
    #: The `index`-th active Banner of a Slot. Later slices let the Owner pick a Banner.
    slot: Literal["home_mid"] = "home_mid"
    index: Annotated[int, Field(ge=0, le=9)] = 0


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
        {"id": "mid-1", "type": "banner", "settings": {"index": 0}},
        {
            "id": "on-sale",
            "type": "products",
            "title": "İndirimdekiler",
            "link": "/ara?on_sale=true",
            "settings": {"source": "on_sale"},
        },
        {"id": "mid-2", "type": "banner", "settings": {"index": 1}},
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
    layout, _ = HomepageLayout.objects.get_or_create(
        pk=1,
        defaults={"sections": default_sections()},
    )
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
    count = section.settings.count
    hero = banners.public()["home_hero"]
    if hero:
        return {"banners": hero[:count]}
    featured = _collection_cards(count)
    return {"products": featured or browse.cards(browse.Filters(), limit=count)}


def _collection_cards(limit: int) -> list[dict]:
    collection = Collection.objects.filter(
        slug=SLIDER_COLLECTION_SLUG,
        status=CollectionStatus.PUBLISHED,
    ).first()
    if collection is None:
        return []
    profiles = collections.visible_products(collection)[:limit]
    return browse.cards_for(profiles)


def _products_data(section: ProductsSection, _mid: list[dict]) -> dict[str, Any]:
    if section.settings.source == "recently_viewed":
        # The list lives in the Shopper's browser (GLOSSARY: Recently viewed).
        return {"client_resolved": True}
    return {"products": _products(section)}


def _categories_data(section: CategoriesSection, _mid: list[dict]) -> dict[str, Any]:
    settings = section.settings
    if settings.source == "picked":
        return {
            "categories": browse.picked_categories(settings.category_ids)[
                : settings.count
            ],
        }
    return {"categories": browse.home_categories()[: settings.count]}


def _banner_data(section: BannerSection, mid: list[dict]) -> dict[str, Any]:
    picked = mid[section.settings.index : section.settings.index + 1]
    return {"banners": picked}


def _rich_text_data(section: RichTextSection, _mid: list[dict]) -> dict[str, Any]:
    body = section.settings.body
    return {"body": body if body.strip() else None}


def _slider_data(section: SliderSection, _mid: list[dict]) -> dict[str, Any]:
    return _slider(section)


_DATA = {
    "slider": _slider_data,
    "products": _products_data,
    "categories": _categories_data,
    "banner": _banner_data,
    "rich_text": _rich_text_data,
}


def _resolve(section: Section, mid: list[dict]) -> dict[str, Any] | None:
    """The Storefront's view of one section, or None when it has nothing to show."""
    data = _DATA[section.type](section, mid)
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


def public() -> dict[str, list[dict[str, Any]]]:
    """The layout as the Storefront draws it: visible sections in order, resolved."""
    return _drawn(validate(get().sections))


def preview() -> dict[str, list[dict[str, Any]]]:
    """The layout a Dashboard user is looking at, drawn the way the Storefront would
    draw it (store-infra#161). Today that is the saved layout; the draft or pending
    layout takes over here once the Approval rule holds one."""
    return _drawn(validate(get().sections))


def _drawn(sections: list[Section]) -> dict[str, list[dict[str, Any]]]:
    mid = banners.public()["home_mid"]
    resolved = (_resolve(s, mid) for s in sections if s.visible)
    return {"sections": [r for r in resolved if r is not None]}


# --- editing -----------------------------------------------------------------------


def for_editing() -> dict[str, list[Section]]:
    """The saved layout as the Dashboard edits it: every section, hidden ones too, with
    its settings, unresolved."""
    return {"sections": validate(get().sections)}


def _normalised(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored sections with defaults filled in, for the audit trail. A document
    that no longer validates is recorded as it is, so it can still be replaced."""
    try:
        return [s.model_dump() for s in validate(raw)]
    except UnprocessableError:
        return raw


@transaction.atomic
def save(actor: Any, raw_sections: list[dict[str, Any]]) -> dict[str, list[Section]]:
    """Replace the whole layout and audit the change. Live at once (Owner only; Staff
    drafts and approval come later)."""
    sections = validate(raw_sections)
    get()
    layout = HomepageLayout.objects.select_for_update().get(pk=1)
    before = _normalised(layout.sections)
    layout.sections = [s.model_dump() for s in sections]
    layout.save()
    audit.record(
        actor,
        audit.Action.HOMEPAGE_SAVED,
        target=layout,
        summary="Vitrin düzeni kaydedildi",
        before={"sections": before},
        after={"sections": layout.sections},
    )
    return {"sections": sections}
