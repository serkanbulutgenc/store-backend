"""Storefront text search: the single swap point if Meilisearch replaces Postgres.

Each ProductProfile carries a folded `search_text` (name, brand, category names,
colours and SKUs) and a Turkish `search_vector` built from it. Folding maps dotted and
dotless i and the Turkish letters to plain ASCII on both the document and the query
side, so "ışık", "ISIK" and "isik" are the same word. Typos are caught by trigram word
similarity on the folded text.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from django.contrib.postgres.search import SearchQuery
from django.contrib.postgres.search import SearchRank
from django.contrib.postgres.search import SearchVector
from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import F
from django.db.models import Q
from django.db.models import Value
from django.db.models.functions import Greatest
from oscar.apps.catalogue.models import ProductCategory

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import VariantProfile

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from oscar.apps.catalogue.models import Product

CONFIG = "turkish"
# Below this word similarity a fuzzy hit is noise; "elbse" vs "elbise" scores ~0.67.
TYPO_THRESHOLD = 0.5

# str.lower() turns "I" into "i" and "İ" into "i" + a combining dot, which is wrong for
# Turkish, so the dotted/dotless pairs are mapped by hand before lowercasing.
_TURKISH = str.maketrans(
    {"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g"},
)


def fold(text: str) -> str:
    """Lowercase and strip diacritics, treating Turkish i/ı/İ/I as one letter."""
    text = text.translate(_TURKISH).lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def document_for(profile: ProductProfile) -> str:
    product = profile.product
    parts = [product.title]
    if profile.brand_id:
        parts.append(profile.brand.name)
    for category in product.categories.all():
        parts.append(category.name)
        if category.depth > 1:
            parts.append(category.get_parent().name)
    for variant in VariantProfile.objects.filter(parent=product).select_related(
        "colour",
    ):
        parts.extend([variant.colour.name, variant.sku])
    return fold(" ".join(parts))


def reindex(product: Product) -> None:
    """Rebuild one Product's search text and vector."""
    profile = ProductProfile.objects.select_related("product", "brand").get(
        product=product,
    )
    text = document_for(profile)
    ProductProfile.objects.filter(pk=profile.pk).update(
        search_text=text,
        search_vector=SearchVector(Value(text), config=CONFIG),
    )


def reindex_all() -> None:
    for profile in ProductProfile.objects.select_related("product"):
        reindex(profile.product)


def reindex_where(**lookup) -> None:
    """Reindex every Product matching a ProductProfile lookup."""
    for profile in ProductProfile.objects.filter(**lookup).select_related("product"):
        reindex(profile.product)


def reindex_category(category) -> None:
    tree = category.get_descendants_and_self()
    ids = ProductCategory.objects.filter(category__in=tree).values("product_id")
    reindex_where(product_id__in=ids)


def search(queryset: QuerySet[ProductProfile], q: str) -> QuerySet[ProductProfile]:
    """Products matching `q` by Turkish full-text or trigram typo tolerance.

    Annotates `relevance` for ordering. A blank query returns the queryset unchanged.
    """
    folded = fold(q)
    if not folded:
        return queryset
    query = SearchQuery(folded, config=CONFIG, search_type="websearch")
    return (
        queryset.annotate(
            _rank=SearchRank(F("search_vector"), query),
            _similarity=TrigramWordSimilarity(folded, "search_text"),
        )
        .filter(Q(search_vector=query) | Q(_similarity__gte=TYPO_THRESHOLD))
        .annotate(relevance=Greatest(F("_rank"), F("_similarity")))
    )
