"""Legal pages: immutable versions, each page's current version, publishing, search.

A page's text is never changed. Publishing inserts the next version, and a Consent
record keeps pointing at the version a Shopper accepted (docs/adr/0002).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.postgres.search import SearchHeadline
from django.contrib.postgres.search import SearchQuery
from django.contrib.postgres.search import SearchRank
from django.contrib.postgres.search import SearchVector
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Max

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.shop.models import LEGAL_PAGE_META
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import LegalPageVersion

if TYPE_CHECKING:
    from store_backend.users.models import User

TITLE_MAX = 200
BODY_MAX = 200_000
MIN_QUERY = 2
SEARCH_LIMIT = 20
SEARCH_CONFIG = "turkish"
# Mark the matched words in an excerpt; plain characters, so no HTML is rendered.
MATCH_START = "«"
MATCH_STOP = "»"


def slug_of(page: str) -> str:
    return LEGAL_PAGE_META[page][0]


def page_for_slug(slug: str) -> str:
    for page, (page_slug, _title) in LEGAL_PAGE_META.items():
        if page_slug == slug:
            return page
    msg = "Sayfa bulunamadı."
    raise NotFoundError(msg)


def _known(page: str) -> str:
    if page not in LEGAL_PAGES:
        msg = "Sayfa bulunamadı."
        raise NotFoundError(msg)
    return page


def current_all() -> list[LegalPageVersion]:
    """The newest version of every page that has one, in the fixed page order."""
    newest = (
        LegalPageVersion.objects.order_by("page", "-version")
        .distinct("page")
        .select_related("created_by")
    )
    by_page = {v.page: v for v in newest}
    return [by_page[p] for p in LEGAL_PAGES if p in by_page]


def current(page: str) -> LegalPageVersion:
    version = (
        LegalPageVersion.objects.filter(page=_known(page))
        .select_related("created_by")
        .order_by("-version")
        .first()
    )
    if version is None:
        msg = "Sayfa bulunamadı."
        raise NotFoundError(msg)
    return version


def current_versions() -> dict[str, str]:
    """`{page: "2"}` for every page: what checkout shows and records."""
    rows = LegalPageVersion.objects.values("page").annotate(v=Max("version"))
    return {row["page"]: str(row["v"]) for row in rows}


def get_version(page: str, number: int) -> LegalPageVersion:
    try:
        return LegalPageVersion.objects.select_related("created_by").get(
            page=_known(page),
            version=number,
        )
    except LegalPageVersion.DoesNotExist:
        msg = "Sürüm bulunamadı."
        raise NotFoundError(msg) from None


def versions(page: str) -> list[LegalPageVersion]:
    return list(
        LegalPageVersion.objects.filter(page=_known(page))
        .select_related("created_by")
        .order_by("-version"),
    )


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


@transaction.atomic
def publish(actor: User, page: str, *, title: str, body: str) -> LegalPageVersion:
    """Save the next version of `page`; it is live at once. 422 when nothing changed."""
    _known(page)
    title = title.strip()
    body = body.strip()
    if not title:
        raise _invalid(field="title", message="Başlık boş olamaz.")
    if len(title) > TITLE_MAX:
        raise _invalid(
            field="title",
            message=f"En fazla {TITLE_MAX} karakter girilebilir.",
        )
    if not body:
        raise _invalid(field="body", message="Metin boş olamaz.")
    if len(body) > BODY_MAX:
        raise _invalid(
            field="body",
            message=f"En fazla {BODY_MAX} karakter girilebilir.",
        )
    # Lock the page's rows so two Owners saving at once cannot both take version N+1.
    existing = list(
        LegalPageVersion.objects.select_for_update()
        .filter(page=page)
        .order_by("-version")[:1],
    )
    latest = existing[0] if existing else None
    if latest and latest.title == title and latest.body.strip() == body:
        raise _invalid(field="body", message="Metinde değişiklik yok.")
    number = latest.version + 1 if latest else 1
    try:
        with transaction.atomic():
            saved = LegalPageVersion.objects.create(
                page=page,
                version=number,
                title=title,
                body=body,
                created_by=actor,
            )
    except IntegrityError:
        msg = (
            "Sayfa az önce başka biri tarafından güncellendi. Yenileyip tekrar deneyin."
        )
        raise ConflictError(msg, code="legal_page_changed") from None
    audit.record(
        actor,
        audit.Action.LEGAL_PAGE_PUBLISHED,
        target=saved,
        summary=f"Yasal sayfa yayınlandı: {title} (sürüm {number})",
        before={"version": latest.version if latest else None},
        after={"version": number, "title": title},
    )
    return saved


def search(text: str) -> list[dict]:
    """Full-text search over the current version of every page, best match first."""
    text = text.strip()
    if len(text) < MIN_QUERY:
        return []
    vector = SearchVector("title", weight="A", config=SEARCH_CONFIG) + SearchVector(
        "body",
        weight="B",
        config=SEARCH_CONFIG,
    )
    query = SearchQuery(text, config=SEARCH_CONFIG, search_type="websearch")
    rows = (
        LegalPageVersion.objects.filter(pk__in=[v.pk for v in current_all()])
        .annotate(
            document=vector,
            rank=SearchRank(vector, query),
            excerpt=SearchHeadline(
                "body",
                query,
                config=SEARCH_CONFIG,
                start_sel=MATCH_START,
                stop_sel=MATCH_STOP,
                max_words=30,
                min_words=12,
            ),
        )
        .filter(document=query)
        .order_by("-rank", "page")[:SEARCH_LIMIT]
    )
    return [
        {
            "page": row.page,
            "slug": slug_of(row.page),
            "title": row.title,
            "version": row.version,
            "excerpt": " ".join(row.excerpt.split()),
        }
        for row in rows
    ]
