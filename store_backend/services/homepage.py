"""The Homepage layout (GLOSSARY: Homepage layout, Homepage section; ADR 0012).

The layout is one document: an ordered list of Homepage sections. Each section has a
type fixed in code and that type's own settings. `public` returns the layout with every
section already resolved for the Storefront; a section that has nothing to show, or
whose target is gone, is left out rather than failing the page.
"""

from __future__ import annotations

from typing import Annotated
from typing import Any
from typing import Literal

from django.db import transaction
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import TypeAdapter
from pydantic import ValidationError

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionStatus
from store_backend.catalog.models import HomepageLayout
from store_backend.services import audit
from store_backend.services import banners
from store_backend.services import browse
from store_backend.services import collections

MAX_SECTIONS = 20
MAX_ITEMS = 24
DEFAULT_ITEMS = 12
SLIDER_ITEMS = 5
#: The Collection feeding the default slider (GLOSSARY: Home slider Collection).
SLIDER_COLLECTION_SLUG = "ana-sayfa"

Display = Literal["grid", "carousel", "list"]
Count = Annotated[int, Field(ge=1, le=MAX_ITEMS)]


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
    source: Literal["latest", "best_selling", "on_sale", "recently_viewed"] = "latest"
    count: Count = DEFAULT_ITEMS


class ProductsSection(_Section):
    type: Literal["products"]
    settings: ProductsSettings = ProductsSettings()


class CategoriesSettings(_Base):
    source: Literal["home"] = "home"
    count: Count = DEFAULT_ITEMS


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


_PRODUCT_SORT = {"latest": "newest", "best_selling": "popular", "on_sale": "newest"}


def _products(section: ProductsSection) -> list[dict]:
    settings = section.settings
    filters = browse.Filters(on_sale=settings.source == "on_sale")
    return browse.cards(
        filters,
        sort=_PRODUCT_SORT[settings.source],
        limit=settings.count,
    )


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
    return {"categories": browse.home_categories()[: section.settings.count]}


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
    sections = validate(get().sections)
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
