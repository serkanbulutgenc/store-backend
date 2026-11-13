# ruff: noqa: TC003
"""Dashboard Legal pages (Owner only): list, read a version, publish the next one."""

from __future__ import annotations

from datetime import datetime

from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires
from store_backend.services import legal_pages
from store_backend.staff.permissions import Perm

router = Router(tags=["legal"])


class AdminLegalRow(Schema):
    page: str
    slug: str
    title: str
    version: int
    created_at: datetime
    created_by: str | None = None


class AdminLegalVersion(AdminLegalRow):
    body: str


class PublishIn(Schema):
    title: str
    body: str


def _row(v) -> dict:
    return {
        "page": v.page,
        "slug": legal_pages.slug_of(v.page),
        "title": v.title,
        "version": v.version,
        "created_at": v.created_at,
        "created_by": v.created_by.email if v.created_by else None,
    }


@router.get(
    "/legal-pages",
    auth=requires(Perm.MANAGE_LEGAL_PAGES),
    operation_id="legal_pages_list",
    response=list[AdminLegalRow],
)
def list_pages(request):
    """The current version of every page."""
    return [_row(v) for v in legal_pages.current_all()]


@router.get(
    "/legal-pages/{page}/versions",
    auth=requires(Perm.MANAGE_LEGAL_PAGES),
    operation_id="legal_pages_versions",
    response=list[AdminLegalRow],
)
def list_versions(request, page: str):
    """Every saved version of one page, newest first."""
    return [_row(v) for v in legal_pages.versions(page)]


@router.get(
    "/legal-pages/{page}/versions/{version}",
    auth=requires(Perm.MANAGE_LEGAL_PAGES),
    operation_id="legal_pages_get_version",
    response=AdminLegalVersion,
)
def get_version(request, page: str, version: int):
    v = legal_pages.get_version(page, version)
    return {**_row(v), "body": v.body}


@router.post(
    "/legal-pages/{page}/versions",
    auth=requires(Perm.MANAGE_LEGAL_PAGES),
    operation_id="legal_pages_publish",
    response={201: AdminLegalVersion},
)
def publish_version(request, page: str, data: PublishIn):
    """Save the next version. It is live on the Storefront at once; 422 if unchanged."""
    v = legal_pages.publish(request.auth, page, title=data.title, body=data.body)
    return 201, {**_row(v), "body": v.body}
