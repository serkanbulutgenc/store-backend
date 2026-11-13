# ruff: noqa: TC003
"""Public Legal pages: read the current text, an older version, or search."""

from __future__ import annotations

from datetime import datetime

from ninja import Query
from ninja import Router
from ninja import Schema

from store_backend.services import legal_pages

router = Router(tags=["legal"])


class LegalPageRow(Schema):
    page: str
    slug: str
    title: str
    version: int


class LegalPageOut(LegalPageRow):
    body: str
    created_at: datetime


class LegalSearchHit(LegalPageRow):
    excerpt: str


class SearchParams(Schema):
    q: str = ""


def _row(v) -> dict:
    return {
        "page": v.page,
        "slug": legal_pages.slug_of(v.page),
        "title": v.title,
        "version": v.version,
    }


def _full(v) -> dict:
    return {**_row(v), "body": v.body, "created_at": v.created_at}


@router.get(
    "/legal-pages",
    auth=None,
    operation_id="legal_list_public",
    response=list[LegalPageRow],
)
def list_pages(request):
    """The current version of every Legal page."""
    return [_row(v) for v in legal_pages.current_all()]


# Declared before `/legal-pages/{slug}` so "search" is never read as a slug.
@router.get(
    "/legal-pages/search",
    auth=None,
    operation_id="legal_search",
    response=list[LegalSearchHit],
)
def search_pages(request, params: Query[SearchParams]):
    """Full-text search over the current Legal pages; matches are marked « »."""
    return legal_pages.search(params.q)


@router.get(
    "/legal-pages/{slug}",
    auth=None,
    operation_id="legal_get_public",
    response=LegalPageOut,
)
def get_page(request, slug: str):
    return _full(legal_pages.current(legal_pages.page_for_slug(slug)))


@router.get(
    "/legal-pages/{slug}/versions/{version}",
    auth=None,
    operation_id="legal_get_public_version",
    response=LegalPageOut,
)
def get_page_version(request, slug: str, version: int):
    """An older text, so a Consent record can show what a Shopper accepted."""
    return _full(legal_pages.get_version(legal_pages.page_for_slug(slug), version))
