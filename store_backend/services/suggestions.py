"""Search suggestions: what the Storefront offers under its search box while a Shopper
types, served by Elasticsearch (ADR 0002 in store-infra). The results page stays on
Postgres (`services.search`).

The index is derived data. A background job re-reads everything that can be suggested
rather than reacting to changes, because several writes emit no signal.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import connection
from elasticsearch import ApiError
from elasticsearch import Elasticsearch
from elasticsearch import TransportError
from elasticsearch import helpers
from elasticsearch.helpers import BulkIndexError

from store_backend.api.errors import ServiceUnavailableError
from store_backend.services import browse
from store_backend.services import catalog
from store_backend.services import search

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

PRODUCT_LIMIT = 6
MIN_QUERY = 2
MAX_QUERY = 100
# With `MAX_EXPANSIONS`, keeps the longest query under Elasticsearch's clause limit.
MAX_WORDS = 10
MAX_EXPANSIONS = 20
# A Shopper is typing: give up rather than keep them (and a web worker) waiting.
QUERY_TIMEOUT = 1
INDEXING_TIMEOUT = 30
LOCK_NAME = "search-suggestions"
# Plural endings, folded. The stemmer misses them where vowel harmony is lost with the
# Turkish letters ("yuzukler" for "yüzükler").
PLURAL_ENDINGS = ("leri", "lari", "ler", "lar")
MIN_ROOT = 3

# Turkish suffixes: "elbiseler" and "elbise" share a stem. `turkish_stem` runs on
# folded text; `turkish_typed` stems a word as the Shopper typed it, then folds it.
_SETTINGS: dict[str, Any] = {
    "analysis": {
        "filter": {
            "turkish_endings": {"type": "stemmer", "language": "turkish"},
            "turkish_lowercase": {"type": "lowercase", "language": "turkish"},
        },
        "analyzer": {
            "turkish_stem": {
                "tokenizer": "standard",
                "filter": ["lowercase", "turkish_endings"],
            },
            "turkish_typed": {
                "tokenizer": "standard",
                "filter": ["turkish_lowercase", "turkish_endings", "asciifolding"],
            },
        },
    },
}
# Folded by `search.fold`, as is what the Shopper types.
_SEARCHABLE: dict[str, Any] = {
    "type": "search_as_you_type",
    "fields": {"stem": {"type": "text", "analyzer": "turkish_stem"}},
}
_MAPPINGS: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "product_id": {"type": "integer"},
        "in_stock": {"type": "boolean"},
        "name": {"type": "keyword", "index": False},
        "name_folded": _SEARCHABLE,
        # Everything the entry can be found by, its name included.
        "text": _SEARCHABLE,
        "path": {"type": "keyword", "index": False},
        "image_url": {"type": "keyword", "index": False},
    },
}


@lru_cache(maxsize=4)
def _client(url: str) -> Elasticsearch:
    return Elasticsearch(url, max_retries=0)


def enabled() -> bool:
    return bool(settings.ELASTICSEARCH_URL)


def _connect(timeout: int) -> Elasticsearch:
    if not enabled():
        raise _unavailable()
    try:
        client = _client(settings.ELASTICSEARCH_URL)
    except ValueError as error:
        logger.warning("ELASTICSEARCH_URL is not usable: %s", error)
        raise _unavailable() from error
    return client.options(request_timeout=timeout)


def _unavailable() -> ServiceUnavailableError:
    return ServiceUnavailableError(code="suggestions_unavailable")


@contextmanager
def _reaching_elasticsearch() -> Iterator[None]:
    """Turn "off, down, busy or not indexed yet" into one error. Anything else is a
    bug and propagates."""
    try:
        yield
    except TransportError as error:
        logger.warning("Elasticsearch is not reachable: %s", error)
        raise _unavailable() from error
    except ApiError as error:
        status = error.meta.status
        expected = (HTTPStatus.NOT_FOUND, HTTPStatus.TOO_MANY_REQUESTS)
        if status not in expected and status < HTTPStatus.INTERNAL_SERVER_ERROR:
            raise
        # Status and type only: the reason can quote what the Shopper typed.
        logger.warning("Elasticsearch cannot answer: %s %s", status, error.error)
        raise _unavailable() from error


class IndexBusyError(Exception):
    """Another run is writing the index."""


@contextmanager
def exclusive() -> Iterator[bool]:
    """One writer of the index at a time, across processes; yields False if another
    holds the lock. A database lock: it has no expiry to outlast, and Postgres frees it
    if the process dies."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", [LOCK_NAME])
        held = cursor.fetchone()[0]
    try:
        yield held
    finally:
        if held:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", [LOCK_NAME])


def _picture(photos: list) -> str | None:
    """The smallest size of the photo a product card would show."""
    image = browse.card_image(photos)
    return image["sizes"][0]["url"] if image and image["sizes"] else None


def _documents():
    products = list(
        browse.published()
        .annotate(has_stock=browse.has_stock())
        .select_related("product"),
    )
    photos = browse.photos_by_product([p.product_id for p in products])
    for profile in products:
        yield {
            "_id": f"product-{profile.product_id}",
            "product_id": profile.product_id,
            "in_stock": profile.has_stock,
            "name": profile.product.title,
            "name_folded": search.fold(profile.product.title),
            # What the results page matches on, kept current by `services.search`.
            "text": profile.search_text,
            "path": catalog.product_path(profile.slug),
            "image_url": _picture(photos[profile.product_id]),
        }


def refresh() -> int:
    """Index everything that can be suggested; returns how many entries. Does nothing
    when switched off or unreachable, or while another run is writing."""
    if not enabled():
        return 0
    with exclusive() as held:
        if not held:
            return 0
        try:
            with _reaching_elasticsearch():
                es = _connect(INDEXING_TIMEOUT)
                alias = settings.SUGGESTIONS_INDEX
                if es.indices.exists_alias(name=alias):
                    return _write(es, alias)
                return _build(es)
        except ServiceUnavailableError:
            return 0
        except BulkIndexError as error:
            # Such as a full disk. The suggestions go stale, so say it loudly.
            logger.error("Search suggestions were not refreshed: %s", error)  # noqa: TRY400
            return 0


def rebuild() -> int:
    """Index everything afresh, as after a change to the mappings. The old index
    answers until the new one is complete. Raises if switched off or unreachable."""
    with exclusive() as held:
        if not held:
            raise IndexBusyError
        with _reaching_elasticsearch():
            return _build(_connect(INDEXING_TIMEOUT))


def _build(es: Elasticsearch) -> int:
    """Fill a new index, point the alias at it alone and drop the others."""
    alias = settings.SUGGESTIONS_INDEX
    index = f"{alias}-{uuid.uuid4().hex[:8]}"
    es.indices.create(index=index, settings=_SETTINGS, mappings=_MAPPINGS)
    try:
        written = _write(es, index)
    except Exception:
        es.options(ignore_status=HTTPStatus.NOT_FOUND).indices.delete(index=index)
        raise
    old = [name for name in es.indices.get(index=f"{alias}-*") if name != index]
    # One atomic step, so there is no moment without an index behind the alias. A
    # failure here leaves the new index alone: the switch may have happened and only
    # its reply been lost. The next build removes whatever is left over.
    es.indices.update_aliases(
        actions=[
            {"add": {"index": index, "alias": alias}},
            *({"remove_index": {"index": name}} for name in old),
        ],
    )
    return written


def _write(es: Elasticsearch, index: str) -> int:
    """Put every current entry in `index` and remove the rest, in place, so
    suggestions stay available throughout."""
    documents = list(_documents())
    current = {d["_id"] for d in documents}
    indexed = helpers.scan(es, index=index, query={"_source": False})
    gone = [
        {"_op_type": "delete", "_id": hit["_id"]}
        for hit in indexed
        if hit["_id"] not in current
    ]
    helpers.bulk(es, [*documents, *gone], index=index, refresh=True)
    return len(documents)


def _word_query(
    field: str,
    word: tuple[str, str],
    *,
    last: bool,
    typos: bool,
) -> dict:
    """One word, as typed and folded: as written or with another Turkish ending, and
    with `typos` also a letter or two away. The last word may still be being typed."""
    raw, folded = word
    stem = f"{field}.stem"
    as_typed = {"query": folded, "operator": "and"}
    stemmed = {**as_typed, "analyzer": "turkish_stem"}
    unfolded = {"query": raw, "operator": "and", "analyzer": "turkish_typed"}
    # The stemmer also trims some singulars ("elbise" to "elbis"), so each side's stem
    # is compared with the other side as written, not only stem with stem.
    ways = [
        {"match": {field: as_typed}},
        {"match": {field: stemmed}},
        {"match": {field: unfolded}},
        {"match": {stem: stemmed}},
        {"match": {stem: unfolded}},
        {"match": {stem: {**as_typed, "analyzer": "standard"}}},
    ]
    ways.extend(
        {"match": {field: {**as_typed, "query": folded.removesuffix(ending)}}}
        for ending in PLURAL_ENDINGS
        if folded.endswith(ending) and len(folded) - len(ending) >= MIN_ROOT
    )
    if last:
        ways.append({"match_bool_prefix": {field: as_typed}})
    # No typo tolerance in codes: one digit apart is another SKU.
    if typos and not any(c.isdigit() for c in folded):
        fuzzy = {"fuzziness": "AUTO", "max_expansions": MAX_EXPANSIONS}
        ways.append({"match": {field: {**as_typed, **fuzzy}}})
    return {"bool": {"should": ways, "minimum_should_match": 1}}


def _every_word_query(
    field: str,
    words: list[tuple[str, str]],
    *,
    typos: bool = True,
) -> dict:
    """All words, in any order."""
    return {
        "bool": {
            "must": [
                _word_query(field, w, last=i == len(words) - 1, typos=typos)
                for i, w in enumerate(words)
            ],
        },
    }


def _words(typed: str) -> list[tuple[str, str]]:
    """Each word as typed and folded. A mark on its own ("-", "&") is not a word: it
    would match nothing."""
    pairs = ((raw, search.fold(raw)) for raw in typed.split())
    return [p for p in pairs if any(c.isalnum() for c in p[1])][:MAX_WORDS]


def suggest(q: str) -> list[dict]:
    """The groups of suggestions for what a Shopper has typed so far."""
    es = _connect(QUERY_TIMEOUT)
    typed = q[:MAX_QUERY].strip()
    words = _words(typed)
    if len(typed) < MIN_QUERY or not words:
        return []
    # Name matches first, and among them names as typed before names a typo away.
    by_name = [
        {
            "constant_score": {
                "filter": _every_word_query("name_folded", words, typos=typos),
                "boost": boost,
            },
        }
        for typos, boost in ((False, 2), (True, 1))
    ]
    with _reaching_elasticsearch():
        response = es.search(
            index=settings.SUGGESTIONS_INDEX,
            query={
                "bool": {
                    "filter": [_every_word_query("text", words)],
                    "should": by_name,
                },
            },
            # In stock before sold out; within each, name matches first, then newest.
            sort=[{"in_stock": "desc"}, "_score", {"product_id": "desc"}],
            size=PRODUCT_LIMIT,
            source_includes=["name", "path", "image_url"],
        )
    items = [hit["_source"] for hit in response["hits"]["hits"]]
    return [{"type": "product", "items": items}] if items else []
