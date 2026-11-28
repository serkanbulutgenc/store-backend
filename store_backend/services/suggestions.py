"""Search suggestions: what the Storefront offers under its search box while a Shopper
types, served by Elasticsearch (ADR 0002 in store-infra). The results page stays on
Postgres (`services.search`).

The index is derived data. A background job re-reads everything that can be suggested
rather than reacting to changes, because several writes emit no signal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
import uuid
from contextlib import contextmanager
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn

from django.conf import settings
from django.db import connection
from elasticsearch import ApiError
from elasticsearch import Elasticsearch
from elasticsearch import TransportError
from elasticsearch import helpers
from elasticsearch.helpers import BulkIndexError

from store_backend.api.errors import ServiceUnavailableError
from store_backend.services import browse
from store_backend.services import campaign_pages
from store_backend.services import catalog
from store_backend.services import categories
from store_backend.services import collections
from store_backend.services import search

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# The groups, in the order a Shopper sees them, and how many each may hold.
GROUP_LIMITS = {
    "category": 3,
    "brand": 3,
    "collection": 3,
    "campaign_page": 3,
    "product": 6,
}
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
# In order; q, w and x are where they fall in the Latin alphabet.
TURKISH_ALPHABET = "abcçdefgğhıijklmnoöpqrsştuüvwxyz"

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
        "type": {"type": "keyword"},
        "item_id": {"type": "integer"},
        "sort_name": {"type": "keyword"},
        "in_stock": {"type": "boolean"},
        "name": {"type": "keyword", "index": False},
        "name_folded": _SEARCHABLE,
        # Everything the entry can be found by, its name included.
        "text": _SEARCHABLE,
        "path": {"type": "keyword", "index": False},
        "image_url": {"type": "keyword", "index": False},
    },
}


# Part of the alias, so an index built to other settings or mappings (an earlier
# release) is never read or written: it counts as not indexed until a build replaces it.
_FORMAT = hashlib.sha256(
    json.dumps([_SETTINGS, _MAPPINGS], sort_keys=True).encode(),
).hexdigest()[:16]


def _alias() -> str:
    return f"{settings.SUGGESTIONS_INDEX}-{_FORMAT}"


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


def _cannot_answer(status: int) -> bool:
    """Not indexed yet, busy or broken, as opposed to a request that is wrong."""
    expected = (HTTPStatus.NOT_FOUND, HTTPStatus.TOO_MANY_REQUESTS)
    return status in expected or status >= HTTPStatus.INTERNAL_SERVER_ERROR


def _log_cannot_answer(status: int, error_type: str) -> None:
    # Status and type only: the reason can quote what the Shopper typed.
    logger.warning("Elasticsearch cannot answer: %s %s", status, error_type)


def _raise_for_failed_search(status: int, error_type: str) -> NoReturn:
    """One search of several failed: the same rule as for a failed request."""
    if not _cannot_answer(status):
        msg = f"Elasticsearch refused a suggestion search: {status} {error_type}"
        raise RuntimeError(msg)
    _log_cannot_answer(status, error_type)
    raise _unavailable()


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
        if not _cannot_answer(error.meta.status):
            raise
        _log_cannot_answer(error.meta.status, error.error)
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


def _turkish_order(name: str) -> str:
    """A key that sorts names by the Turkish alphabet, ignoring case and any other
    accent. Spaces and digits stay before the letters."""
    lowered = unicodedata.normalize("NFC", name).replace("I", "ı").replace("İ", "i")
    return "".join(_letter_order(c) for c in lowered.lower())


def _letter_order(character: str) -> str:
    if character not in TURKISH_ALPHABET:
        character = unicodedata.normalize("NFD", character)[0]
    position = TURKISH_ALPHABET.find(character)
    # Above ASCII, so no digit or mark can fall among the letters.
    return character if position < 0 else chr(0x100 + position)


def _entry(  # noqa: PLR0913
    kind: str,
    item_id: int,
    name: str,
    *,
    path: str,
    image_url: str | None,
    text: str | None = None,
    in_stock: bool = True,
) -> dict:
    """One index document. Without `text` it is found by its name alone."""
    folded = search.fold(name)
    return {
        "_id": f"{kind}-{item_id}",
        "type": kind,
        "item_id": item_id,
        "in_stock": in_stock,
        "name": name,
        "name_folded": folded,
        "sort_name": _turkish_order(name),
        "text": folded if text is None else text,
        "path": path,
        "image_url": image_url,
    }


def _picture(image: dict | None) -> str | None:
    """The smallest size of an image the Storefront shows, if there is one."""
    if not image or not image["sizes"]:
        return None
    return min(image["sizes"], key=lambda s: s["width"])["url"]


def _categories() -> Iterator[dict]:
    for category in categories.visible():
        yield _entry(
            "category",
            category.pk,
            category.name,
            path=categories.path(category),
            image_url=_picture(categories.cover_image(category)),
        )


def _brands() -> Iterator[dict]:
    for brand in browse.public_brands():
        yield _entry(
            "brand",
            brand.pk,
            brand.name,
            path=browse.brand_path(brand.pk),
            image_url=brand.logo_url(),
        )


def _collections() -> Iterator[dict]:
    for collection in collections.public():
        yield _entry(
            "collection",
            collection.pk,
            collection.name,
            path=collections.path(collection),
            image_url=_picture(collections.cover_image(collection)),
        )


def _campaign_pages() -> Iterator[dict]:
    for page in campaign_pages.running():
        yield _entry(
            "campaign_page",
            page.pk,
            page.title,
            path=campaign_pages.path(page),
            image_url=_picture(campaign_pages.cover_image(page)),
        )


def _products() -> Iterator[dict]:
    products = list(
        browse.published()
        .annotate(has_stock=browse.has_stock())
        .select_related("product"),
    )
    photos = browse.photos_by_product([p.product_id for p in products])
    for profile in products:
        # The photo a product card would show.
        image = browse.card_image(photos[profile.product_id])
        yield _entry(
            "product",
            profile.product_id,
            profile.product.title,
            path=catalog.product_path(profile.slug),
            image_url=_picture(image),
            # What the results page matches on, kept current by `services.search`.
            text=profile.search_text,
            in_stock=profile.has_stock,
        )


def _documents() -> Iterator[dict]:
    """Everything that can be suggested now."""
    yield from _categories()
    yield from _brands()
    yield from _collections()
    yield from _campaign_pages()
    yield from _products()


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
                if es.indices.exists_alias(name=_alias()):
                    return _write(es, _alias())
                return _build(es)
        except ServiceUnavailableError:
            return 0
        except BulkIndexError as error:
            # Such as a full disk. The suggestions go stale, so say it loudly.
            logger.error("Search suggestions were not refreshed: %s", error)  # noqa: TRY400
            return 0


def rebuild() -> int:
    """Index everything afresh, on an operator's request. The old index answers until
    the new one is complete. Raises if switched off or unreachable."""
    with exclusive() as held:
        if not held:
            raise IndexBusyError
        with _reaching_elasticsearch():
            return _build(_connect(INDEXING_TIMEOUT))


def _build(es: Elasticsearch) -> int:
    """Fill a new index, point the alias at it alone and drop the others."""
    alias = _alias()
    index = f"{alias}-{uuid.uuid4().hex[:8]}"
    es.indices.create(index=index, settings=_SETTINGS, mappings=_MAPPINGS)
    try:
        written = _write(es, index)
    except Exception:
        es.options(ignore_status=HTTPStatus.NOT_FOUND).indices.delete(index=index)
        raise
    # Every other index, those of earlier formats too (their aliases go with them).
    others = es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*")
    old = [name for name in others if name != index]
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


def _search_for(kind: str, words: list[tuple[str, str]]) -> dict:
    """The search for one group."""
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
    # Products: in stock before sold out, then the newest. The rest: alphabetical.
    ties = (
        [{"item_id": "desc"}]
        if kind == "product"
        else [{"sort_name": "asc"}, {"item_id": "asc"}]
    )
    return {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"type": kind}},
                    _every_word_query("text", words),
                ],
                "should": by_name,
            },
        },
        "sort": [{"in_stock": "desc"}, "_score", *ties],
        "size": GROUP_LIMITS[kind],
        "_source": ["name", "path", "image_url"],
    }


def suggest(q: str) -> list[dict]:
    """The groups of suggestions for what a Shopper has typed so far."""
    es = _connect(QUERY_TIMEOUT)
    typed = q[:MAX_QUERY].strip()
    words = _words(typed)
    if len(typed) < MIN_QUERY or not words:
        return []
    header = {"index": _alias()}
    searches = [
        line for kind in GROUP_LIMITS for line in (header, _search_for(kind, words))
    ]
    with _reaching_elasticsearch():
        responses = es.msearch(searches=searches)["responses"]
        for response in responses:
            if "error" in response:
                _raise_for_failed_search(
                    response["status"],
                    response["error"]["type"],
                )
    groups = []
    for kind, response in zip(GROUP_LIMITS, responses, strict=True):
        if items := [hit["_source"] for hit in response["hits"]["hits"]]:
            groups.append({"type": kind, "items": items})
    return groups
