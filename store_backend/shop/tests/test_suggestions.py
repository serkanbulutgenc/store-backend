# ruff: noqa: PLR2004
"""Search suggestions (store-infra#185), against a real Elasticsearch."""

from __future__ import annotations

import itertools
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from elasticsearch import ConnectionTimeout
from elasticsearch import Elasticsearch
from elasticsearch.client import IndicesClient
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.conftest import add_ready_photo
from store_backend.services import attributes
from store_backend.services import catalog
from store_backend.services import stock
from store_backend.services import suggestions
from store_backend.shop import tasks
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

URL = "/api/store/suggestions"


@pytest.fixture(autouse=True)
def elasticsearch(settings):
    """A running Elasticsearch and an index of its own for each test. Missing means
    failure, not a skip, so CI cannot pass without testing search."""
    url = os.environ.get("ELASTICSEARCH_URL")
    if not url:
        pytest.fail("Set ELASTICSEARCH_URL: these tests need a running Elasticsearch.")
    settings.ELASTICSEARCH_URL = url
    settings.SUGGESTIONS_INDEX = f"test-suggestions-{uuid.uuid4().hex}"
    yield
    with Elasticsearch(url) as client:
        for name in client.indices.get(index=f"{settings.SUGGESTIONS_INDEX}*"):
            client.indices.delete(index=name)


def plain_category():
    """A Category and (below) a Colour whose names no test searches for."""
    found = Category.objects.filter(name="Giyim").first()
    return found or catalog.create_category("Giyim")


def plain_colour():
    found = Colour.objects.filter(name="Siyah").first()
    return found or catalog.create_colour("Siyah", "#000000")


def make(title, *, publish=True, brand=None, category=None, colour=None, stock=3):  # noqa: PLR0913
    product = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=category or plain_category(),
        shelf_price="100.00",
        brand=brand,
    )
    catalog.create_variant(
        product,
        colour=colour or plain_colour(),
        size="M",
        stock=stock,
    )
    if publish:
        add_ready_photo(product)
        catalog.set_status(product, ProductStatus.PUBLISHED)
    return product


def groups(client, q):
    response = client.get(URL, {"q": q})
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["groups"]


def names(client, q):
    return [item["name"] for group in groups(client, q) for item in group["items"]]


def test_a_published_product_is_suggested_by_its_name(client):
    product = make("Keten Elbise")

    tasks.refresh_suggestions()

    assert groups(client, "keten") == [
        {
            "type": "product",
            "items": [
                {
                    "name": "Keten Elbise",
                    "path": f"/urun/{product.profile.slug}",
                    "image_url": None,
                },
            ],
        },
    ]


@pytest.mark.parametrize(
    ("q", "title"),
    [
        ("canta", "Deri Çanta"),
        ("ÇANTA", "Deri Çanta"),
        ("IŞIK", "ışık Kolye"),
        ("isik", "ışık Kolye"),
        ("ipek", "İpek Fular"),
    ],
)
def test_turkish_letters_and_dotted_i_are_ignored(client, q, title):
    make(title)
    tasks.refresh_suggestions()

    assert names(client, q) == [title]


@pytest.mark.parametrize(
    "q",
    [
        "keten elbise",
        "elbise keten",  # any order
        "keten elb",  # the last word still being typed
        "elbise ke",
        "el",
    ],
)
def test_every_typed_word_matches_and_the_last_may_be_incomplete(client, q):
    make("Keten Elbise")
    make("Saten Bluz")
    tasks.refresh_suggestions()

    assert names(client, q) == ["Keten Elbise"]


def test_all_typed_words_must_match(client):
    make("Keten Elbise")
    tasks.refresh_suggestions()

    assert names(client, "keten bluz") == []


@pytest.mark.parametrize(
    ("q", "title"),
    [
        ("elbse", "Keten Elbise"),  # a letter missing
        ("elbize", "Keten Elbise"),  # a wrong letter
        ("ketne elbise", "Keten Elbise"),  # swapped letters
        ("elbiseler", "Keten Elbise"),
        ("gomlekler", "Saten Gömlek"),
        ("çantalar", "Deri Çanta"),
        ("keten elbiseler", "Keten Elbise"),
        ("kazak", "Triko Kazaklar"),  # the name carries the ending
    ],
)
def test_typos_and_turkish_word_endings_still_match(client, q, title):
    for name in ("Keten Elbise", "Saten Gömlek", "Deri Çanta", "Triko Kazaklar"):
        make(name)
    tasks.refresh_suggestions()

    assert names(client, q) == [title]


@pytest.fixture
def tunic():
    """A Product whose name shares no word with what else it can be found by."""
    parent = catalog.create_category("Kadın")
    product = make(
        "Salaş Tunik",
        brand=catalog.create_brand("Zeytindalı"),
        category=catalog.create_category("Üst Giyim", parent=parent),
        colour=catalog.create_colour("Bordo", "#800000"),
    )
    material = attributes.create(
        staff_member("Owner"),
        product.product_class.pk,
        name="Kumaş",
        kind="option",
        options=["Viskon"],
    )
    attributes.set_values(
        product,
        [
            attributes.ValueInput(
                attribute_id=material["id"],
                option_ids=[material["options"][0]["id"]],
            ),
        ],
    )
    return product


@pytest.mark.parametrize(
    "q",
    [
        "zeytindali",  # Brand
        "üst giyim",  # Category
        "kadın",  # its parent
        "bordo",  # colour name
        "viskon",  # attribute value
        "bordo tunik",  # words from two places
    ],
)
def test_a_product_is_found_by_what_the_results_page_matches(client, tunic, q):
    tasks.refresh_suggestions()

    assert names(client, q) == ["Salaş Tunik"]


def test_a_product_is_found_by_its_sku(client, tunic):
    sku = tunic.children.get().variant_profile.sku
    tasks.refresh_suggestions()

    assert names(client, sku) == ["Salaş Tunik"]
    assert names(client, sku[:-2]) == ["Salaş Tunik"]


def test_a_whole_sku_does_not_suggest_products_with_similar_codes(client):
    products = [make(f"Basic Tişört {n}") for n in range(3)]
    sku = products[1].children.get().variant_profile.sku
    tasks.refresh_suggestions()

    assert names(client, sku) == ["Basic Tişört 1"]


def test_in_stock_products_come_first_and_name_matches_first_within_each(client):
    brand = catalog.create_brand("Keten Evi")
    # Created in an order that matches none of the expected one.
    make("Keten Bluz", stock=0)
    make("Yazlık Etek", brand=brand)
    make("Keten Elbise")
    make("Düz Şort", brand=brand, stock=0)
    tasks.refresh_suggestions()

    assert names(client, "keten") == [
        "Keten Elbise",  # in stock, by name
        "Yazlık Etek",  # in stock, by Brand
        "Keten Bluz",  # sold out, by name
        "Düz Şort",  # sold out, by Brand
    ]


def test_at_most_six_products_are_suggested(client):
    for n in range(8):
        make(f"Pamuk Atlet {n}")
    tasks.refresh_suggestions()

    assert len(names(client, "atlet")) == 6


@pytest.mark.parametrize("q", ["", "k", " k ", "-- !"])
def test_nothing_is_suggested_for_under_two_characters_or_no_words(client, q):
    make("Keten Elbise")
    tasks.refresh_suggestions()

    assert groups(client, q) == []


def test_only_the_first_100_characters_of_a_query_count(client):
    make("Keten Elbise")
    tasks.refresh_suggestions()
    padded = "keten" + " " * 95

    assert names(client, padded + "bluz") == ["Keten Elbise"]
    assert names(client, padded[:-5] + "bluz") == []


def test_a_query_of_very_many_words_is_answered(client):
    tasks.refresh_suggestions()

    assert groups(client, "ab " * 40) == []


def add_photo(product, name, *, primary, status=ProductPhoto.Status.READY):
    return ProductPhoto.objects.create(
        product=product,
        original=f"products/{name}.jpg",
        width=1000,
        height=1000,
        status=status,
        is_primary=primary,
        sort_order=9,
        derivatives=[
            {"width": width, "name": f"products/{name}-{width}.webp"}
            for width in (1600, 400, 800)
        ],
    )


def image_url(client):
    return groups(client, "keten")[0]["items"][0]["image_url"]


def test_the_picture_is_the_card_size_of_the_primary_ready_photo(client):
    product = make("Keten Elbise", publish=False)
    add_photo(product, "yan", primary=False)
    add_photo(product, "on", primary=True)
    catalog.set_status(product, ProductStatus.PUBLISHED)
    tasks.refresh_suggestions()

    assert image_url(client) == "http://media.testserver/products/on-400.webp"


def test_another_ready_photo_stands_in_while_the_primary_is_processing(client):
    product = make("Keten Elbise", publish=False)
    add_photo(product, "on", primary=True, status=ProductPhoto.Status.PROCESSING)
    add_photo(product, "yan", primary=False)
    catalog.set_status(product, ProductStatus.PUBLISHED)
    tasks.refresh_suggestions()

    assert image_url(client) == "http://media.testserver/products/yan-400.webp"


# --- the refresh job -------------------------------------------------------------


def refreshed_names(client, q="keten"):
    tasks.refresh_suggestions()
    return names(client, q)


def test_publishing_shows_up_after_one_refresh(client):
    product = make("Keten Elbise", publish=False)
    assert refreshed_names(client) == []

    add_ready_photo(product)
    catalog.set_status(product, ProductStatus.PUBLISHED)

    assert refreshed_names(client) == ["Keten Elbise"]


@pytest.mark.parametrize("status", [ProductStatus.DRAFT, ProductStatus.ARCHIVED])
def test_a_product_no_longer_published_is_removed(client, status):
    product = make("Keten Elbise")
    make("Keten Bluz")
    assert refreshed_names(client) == ["Keten Bluz", "Keten Elbise"]

    catalog.set_status(product, status)

    assert refreshed_names(client) == ["Keten Bluz"]


def test_a_deleted_product_is_removed(client):
    product = make("Keten Elbise")
    assert refreshed_names(client) == ["Keten Elbise"]

    catalog.delete_product(product)

    assert refreshed_names(client) == []


def test_a_new_name_replaces_the_old_one(client):
    product = make("Keten Elbise")
    tasks.refresh_suggestions()

    catalog.update_product(staff_member("Owner"), product, title="Keten Tulum")
    tasks.refresh_suggestions()

    assert names(client, "keten") == ["Keten Tulum"]
    assert names(client, "elbise") == []


def test_a_stock_change_reorders_after_one_refresh(client):
    make("Keten Elbise")
    newer = make("Keten Bluz")
    assert refreshed_names(client) == ["Keten Bluz", "Keten Elbise"]

    sold_out = newer.children.get()
    stock.adjust(staff_member("Owner"), sold_out, delta=-3, reason="Sayım")

    assert refreshed_names(client) == ["Keten Elbise", "Keten Bluz"]


def test_a_new_primary_photo_shows_up_after_one_refresh(client):
    product = make("Keten Elbise")
    tasks.refresh_suggestions()
    assert image_url(client) is None

    ProductPhoto.objects.filter(product=product).update(is_primary=False)
    add_photo(product, "on", primary=True)
    tasks.refresh_suggestions()

    assert image_url(client) == "http://media.testserver/products/on-400.webp"


@contextmanager
def another_writer():
    """Hold the lock from a second database session, as another process would."""
    holding, done, held = threading.Event(), threading.Event(), []

    def hold():
        try:
            with suggestions.exclusive() as got:
                held.append(got)
                holding.set()
                done.wait(30)
        finally:
            holding.set()
            connection.close()

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(30)
        assert held == [True]
        yield
    finally:
        done.set()
        thread.join()


def test_a_refresh_is_skipped_while_another_holds_the_lock(client):
    make("Keten Elbise")
    with another_writer():
        assert tasks.refresh_suggestions() == 0
    # Nothing was written, and the lock is free again afterwards.
    assert client.get(URL, {"q": "keten"}).status_code == 503

    assert refreshed_names(client) == ["Keten Elbise"]


def test_a_finished_refresh_frees_the_lock_for_other_processes():
    tasks.refresh_suggestions()

    with another_writer():  # asserts that it got the lock
        pass


def test_enabled_but_not_yet_indexed_is_unavailable(client):
    response = client.get(URL, {"q": "keten"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == "suggestions_unavailable"


# --- off or unreachable ----------------------------------------------------------


@pytest.fixture(
    params=["", "http://127.0.0.1:1", "elasticsearch:9200"],
    ids=["off", "unreachable", "no-scheme"],
)
def without_elasticsearch(settings, request):
    settings.ELASTICSEARCH_URL = request.param


def test_without_elasticsearch_suggestions_are_unavailable(
    client,
    without_elasticsearch,
):
    response = client.get(URL, {"q": "keten"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == "suggestions_unavailable"


def test_without_elasticsearch_the_refresh_does_nothing(without_elasticsearch):
    make("Keten Elbise")

    assert tasks.refresh_suggestions() == 0


def test_a_server_that_does_not_answer_is_given_up_on_quickly(client, settings):
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        settings.ELASTICSEARCH_URL = f"http://127.0.0.1:{silent.getsockname()[1]}"
        started = time.monotonic()
        response = client.get(URL, {"q": "keten"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert time.monotonic() - started < 3


# --- the rebuild command ---------------------------------------------------------


def test_rebuilding_makes_suggestions_available_from_nothing(client):
    make("Keten Elbise")

    call_command("rebuild_suggestions", stdout=StringIO())

    assert names(client, "keten") == ["Keten Elbise"]


def test_rebuilding_replaces_what_was_indexed_and_leaves_one_index(client, settings):
    product = make("Keten Elbise")
    tasks.refresh_suggestions()
    catalog.delete_product(product)
    make("Keten Bluz")

    call_command("rebuild_suggestions", stdout=StringIO())

    assert names(client, "keten") == ["Keten Bluz"]
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        assert len(es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*").body) == 1


def test_a_rebuild_that_fails_partway_keeps_the_old_suggestions(
    client,
    settings,
    monkeypatch,
):
    make("Keten Elbise")
    tasks.refresh_suggestions()
    make("Keten Bluz")

    def dies_partway():
        yield {"_id": "product-0", "name": "Yarım", "name_folded": "keten yarim"}
        msg = "database went away"
        raise RuntimeError(msg)

    monkeypatch.setattr(suggestions, "_documents", dies_partway)
    with pytest.raises(RuntimeError):
        call_command("rebuild_suggestions", stdout=StringIO())

    assert names(client, "keten") == ["Keten Elbise"]
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        assert len(es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*").body) == 1


def test_rebuilding_is_refused_while_a_refresh_is_writing():
    with another_writer(), pytest.raises(CommandError, match="already"):
        call_command("rebuild_suggestions")


def test_rebuilding_says_so_when_elasticsearch_is_off(without_elasticsearch):
    with pytest.raises(CommandError, match=r"ELASTICSEARCH_URL|cannot be reached"):
        call_command("rebuild_suggestions")


# --- found in review -------------------------------------------------------------


@pytest.mark.parametrize(
    "q",
    [
        "keten - elbise",
        "keten elbise -",
        "keten & elbise",
        "keten + elbise",
        "keten / elbise",
        "( keten",
    ],
)
def test_punctuation_on_its_own_is_ignored(client, q):
    make("Keten Elbise")
    tasks.refresh_suggestions()

    assert names(client, q) == ["Keten Elbise"]


@pytest.mark.parametrize(
    ("q", "title"),
    [
        ("yüzükler", "Altın Yüzük"),
        ("yuzukler", "Altın Yüzük"),
        ("gözlükler", "Güneş Gözlük"),
        ("üstlükler", "İnce Üstlük"),
        ("eteği", "Uzun Etek"),
        ("yüzükleri", "Altın Yüzük"),
    ],
)
def test_endings_after_rounded_vowels_and_softened_consonants(client, q, title):
    for name in ("Altın Yüzük", "Güneş Gözlük", "İnce Üstlük", "Uzun Etek"):
        make(name)
    tasks.refresh_suggestions()

    assert names(client, q)[:1] == [title]


def test_a_name_as_typed_comes_before_names_a_typo_away(client):
    make("Kot Pantolon")
    for n in range(6):  # newer, and only a letter away
        make(f"Deri Bot {n}")
    tasks.refresh_suggestions()

    assert names(client, "kot")[0] == "Kot Pantolon"


def test_a_long_query_over_many_similar_words_is_answered(client):
    product = make("Keten Elbise")
    letters = "abcdefghijklmnopqrst"
    similar = ["".join(three) for three in itertools.product(letters, repeat=3)]
    ProductProfile.objects.filter(product=product).update(
        search_text=" ".join(similar),
    )
    tasks.refresh_suggestions()

    assert names(client, " ".join(similar[::317][:25])) == ["Keten Elbise"]


@pytest.mark.parametrize("url", ["", "http://127.0.0.1:1"], ids=["off", "down"])
def test_being_unavailable_is_not_logged_as_an_error(client, settings, caplog, url):
    settings.ELASTICSEARCH_URL = url

    response = client.get(URL, {"q": "gizli arama"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert "gizli" not in caplog.text


def test_an_index_that_refuses_writes_does_not_fail_the_job(client, settings):
    make("Keten Elbise")
    tasks.refresh_suggestions()
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        es.indices.put_settings(
            index=settings.SUGGESTIONS_INDEX,
            settings={"index.blocks.write": True},
        )

    assert tasks.refresh_suggestions() == 0
    assert names(client, "keten") == ["Keten Elbise"]


def test_a_rebuild_the_server_refuses_to_fill_says_so(monkeypatch):
    def unacceptable():
        yield {"_id": "product-0", "unknown_field": 1}

    monkeypatch.setattr(suggestions, "_documents", unacceptable)

    with pytest.raises(CommandError, match="refused"):
        call_command("rebuild_suggestions", stdout=StringIO())


def test_a_lost_reply_to_the_switch_does_not_delete_the_new_index(client, monkeypatch):
    make("Keten Elbise")
    tasks.refresh_suggestions()
    make("Keten Bluz")
    switch = IndicesClient.update_aliases

    def switch_then_lose_the_reply(self, **kwargs):
        switch(self, **kwargs)
        msg = "timed out"
        raise ConnectionTimeout(msg)

    monkeypatch.setattr(IndicesClient, "update_aliases", switch_then_lose_the_reply)
    with pytest.raises(CommandError, match="cannot be reached"):
        call_command("rebuild_suggestions", stdout=StringIO())

    assert names(client, "keten") == ["Keten Bluz", "Keten Elbise"]
