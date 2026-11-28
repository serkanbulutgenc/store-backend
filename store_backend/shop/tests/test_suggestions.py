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
from datetime import timedelta
from http import HTTPStatus
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone
from elasticsearch import ConnectionTimeout
from elasticsearch import Elasticsearch
from elasticsearch.client import IndicesClient
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Brand
from store_backend.catalog.models import CampaignPage
from store_backend.catalog.models import CategoryProfile
from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.conftest import add_ready_photo
from store_backend.services import attributes
from store_backend.services import catalog
from store_backend.services import stock
from store_backend.services import suggestions
from store_backend.services.catalog import slugify_tr
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


def items(client, q, kind="product"):
    """The suggestions of one type, empty when it has no group."""
    return [
        item
        for group in groups(client, q)
        if group["type"] == kind
        for item in group["items"]
    ]


def names(client, q, kind="product"):
    return [item["name"] for item in items(client, q, kind)]


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
            index=f"{settings.SUGGESTIONS_INDEX}-*",
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


# --- the other types (store-infra#186) -------------------------------------------


def picture(name, *, status="ready"):
    """The image columns of a Category, Collection or Campaign page."""
    return {
        "image_original": f"x/{name}.jpg",
        "image_status": status,
        "image_derivatives": [
            {"width": width, "name": f"x/{name}-{width}.webp"} for width in (800, 400)
        ],
    }


def test_a_category_is_suggested_even_with_no_products(client):
    parent = catalog.create_category("Kadın")
    category = catalog.create_category("Abiye", parent=parent)
    tasks.refresh_suggestions()

    assert groups(client, "abiye") == [
        {
            "type": "category",
            "items": [
                {
                    "name": "Abiye",
                    "path": f"/kategori/{category.slug}",
                    "image_url": None,
                },
            ],
        },
    ]


@pytest.mark.parametrize("hidden", ["itself", "parent"])
def test_a_hidden_category_or_one_under_a_hidden_one_is_left_out(client, hidden):
    parent = catalog.create_category("Kadın")
    category = catalog.create_category("Abiye", parent=parent)
    tasks.refresh_suggestions()

    target = category if hidden == "itself" else parent
    CategoryProfile.objects.filter(category=target).update(is_hidden=True)
    tasks.refresh_suggestions()

    assert names(client, "abiye", "category") == []


def test_the_other_types_match_by_name_only(client):
    parent = catalog.create_category("Kadın")
    catalog.create_category("Abiye", parent=parent)
    tasks.refresh_suggestions()

    assert names(client, "kadın", "category") == ["Kadın"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "http://media.testserver/x/abiye-400.webp"),
        ("processing", None),
    ],
)
def test_a_category_picture_is_its_smallest_ready_size(client, status, expected):
    category = catalog.create_category("Abiye")
    CategoryProfile.objects.filter(category=category).update(
        **picture("abiye", status=status),
    )
    tasks.refresh_suggestions()

    assert items(client, "abiye", "category")[0]["image_url"] == expected


def test_a_brand_with_a_published_product_leads_to_its_search_results(client):
    brand = catalog.create_brand("Zeytindalı")
    make("Keten Elbise", brand=brand, stock=0)  # sold out still counts
    tasks.refresh_suggestions()

    assert items(client, "zeytin", "brand") == [
        {"name": "Zeytindalı", "path": f"/ara?brand={brand.pk}", "image_url": None},
    ]


@pytest.mark.parametrize("products", ["none", "draft", "archived"])
def test_a_brand_without_a_published_product_is_left_out(client, products):
    brand = catalog.create_brand("Zeytindalı")
    if products != "none":
        product = make("Keten Elbise", brand=brand)
        catalog.set_status(product, ProductStatus(products))
    tasks.refresh_suggestions()

    assert names(client, "zeytin", "brand") == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "http://media.testserver/x/logo-400.webp"),
        ("processing", None),
    ],
)
def test_a_brand_picture_is_its_smallest_ready_logo(client, status, expected):
    brand = catalog.create_brand("Zeytindalı")
    make("Keten Elbise", brand=brand)
    Brand.objects.filter(pk=brand.pk).update(
        logo_original="x/logo.png",
        logo_status=status,
        # Smallest first, as the logo job stores them.
        logo_derivatives=[
            {"width": width, "name": f"x/logo-{width}.webp"} for width in (400, 800)
        ],
    )
    tasks.refresh_suggestions()

    assert items(client, "zeytin", "brand")[0]["image_url"] == expected


def make_collection(name="Yazlık Seçki", *products, status="published", **fields):
    collection = Collection.objects.create(
        name=name,
        slug=slugify_tr(name),
        status=status,
        **fields,
    )
    for position, product in enumerate(products):
        CollectionProduct.objects.create(
            collection=collection,
            product=product,
            position=position,
        )
    return collection


def test_a_published_collection_with_a_visible_product_is_suggested(client):
    collection = make_collection("Yazlık Seçki", make("Keten Elbise", stock=0))
    tasks.refresh_suggestions()

    assert items(client, "secki", "collection") == [
        {
            "name": "Yazlık Seçki",
            "path": f"/collections/{collection.slug}",
            "image_url": None,
        },
    ]


@pytest.mark.parametrize("why", ["draft", "pending", "empty", "no visible product"])
def test_a_collection_the_storefront_does_not_show_is_left_out(client, why):
    if why == "empty":
        make_collection()
    elif why == "no visible product":
        make_collection("Yazlık Seçki", make("Keten Elbise", publish=False))
    else:
        make_collection("Yazlık Seçki", make("Keten Elbise"), status=why)
    tasks.refresh_suggestions()

    assert names(client, "secki", "collection") == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "http://media.testserver/x/kapak-400.webp"),
        ("failed", None),
    ],
)
def test_a_collection_picture_is_its_smallest_ready_cover(client, status, expected):
    make_collection(
        "Yazlık Seçki",
        make("Keten Elbise"),
        **picture("kapak", status=status),
    )
    tasks.refresh_suggestions()

    assert items(client, "secki", "collection")[0]["image_url"] == expected


def make_page(title="Bahar İndirimi", *, status="published", **fields):
    return CampaignPage.objects.create(
        title=title,
        slug=slugify_tr(title),
        status=status,
        **fields,
    )


def day(offset):
    return timezone.now() + timedelta(days=offset)


@pytest.mark.parametrize(
    ("starts", "ends"),
    [(None, None), (-1, None), (None, 1), (-1, 1)],
)
def test_a_published_started_campaign_page_is_suggested(client, starts, ends):
    page = make_page(
        starts_at=None if starts is None else day(starts),
        ends_at=None if ends is None else day(ends),
    )
    tasks.refresh_suggestions()

    assert items(client, "bahar", "campaign_page") == [
        {
            "name": "Bahar İndirimi",
            "path": f"/kampanyalar/{page.slug}",
            "image_url": None,
        },
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "draft"},
        {"status": "pending"},
        {"starts_at": 1},  # not started
        {"ends_at": -1},  # ended: its URL still works, but it is not suggested
    ],
    ids=["draft", "pending", "not started", "ended"],
)
def test_a_campaign_page_not_running_publicly_is_left_out(client, fields):
    make_page(
        **{k: day(v) if k.endswith("_at") else v for k, v in fields.items()},
    )
    tasks.refresh_suggestions()

    assert names(client, "bahar", "campaign_page") == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "http://media.testserver/x/afis-400.webp"),
        ("processing", None),
    ],
)
def test_a_campaign_page_picture_is_its_smallest_ready_cover(client, status, expected):
    make_page(**picture("afis", status=status))
    tasks.refresh_suggestions()

    assert items(client, "bahar", "campaign_page")[0]["image_url"] == expected


def everything_called(word, count):
    """`count` of each type, all with `word` in their name."""
    brand = None
    for n in range(count):
        catalog.create_category(f"{word} Kategori {n}")
        brand = catalog.create_brand(f"{word} Marka {n}")
        product = make(f"{word} Ürün {n}", brand=brand)
        make_collection(f"{word} Seçki {n}", product)
        make_page(f"{word} Kampanya {n}")


def test_groups_come_in_order_with_at_most_three_each_and_six_products(client):
    everything_called("Nar", 8)
    tasks.refresh_suggestions()

    found = groups(client, "nar")

    assert [(g["type"], len(g["items"])) for g in found] == [
        ("category", 3),
        ("brand", 3),
        ("collection", 3),
        ("campaign_page", 3),
        ("product", 6),
    ]


def test_empty_groups_are_left_out(client):
    catalog.create_category("Nar Kategori")
    make_page("Nar Kampanya")
    tasks.refresh_suggestions()

    assert [g["type"] for g in groups(client, "nar")] == ["category", "campaign_page"]


def test_names_as_typed_come_first_then_the_rest_alphabetically(client):
    for name in ("Kol Çantası", "Bot Bakımı", "Kot Şort", "Kot Ceket"):
        catalog.create_category(name)
    tasks.refresh_suggestions()

    assert names(client, "kot", "category") == [
        "Kot Ceket",
        "Kot Şort",
        "Bot Bakımı",  # a letter away
    ]


@pytest.mark.parametrize(
    "ordered",
    [
        # c before ç, s before ş; the limit of three leaves out the last.
        ["Nar Cüzdan", "Nar Çanta", "Nar Sandalet", "Nar Şal"],
        # Dotless ı comes before dotted i.
        ["Nar Hırka", "Nar Işık", "Nar İnci"],
        # An accented letter sorts with its base letter.
        ["Nar Kağıt", "Nar Kâse", "Nar Kemer"],
    ],
)
def test_alphabetical_means_the_turkish_alphabet(client, ordered):
    for name in reversed(ordered):
        catalog.create_category(name)
    tasks.refresh_suggestions()

    assert names(client, "nar", "category") == ordered[:3]


# --- changes no signal reports ---------------------------------------------------


def test_new_covers_and_logos_show_up_after_one_refresh(client):
    brand = catalog.create_brand("Nar Marka")
    product = make("Keten Elbise", brand=brand)
    category = catalog.create_category("Nar Kategori")
    collection = make_collection("Nar Seçki", product)
    page = make_page("Nar Kampanya")
    tasks.refresh_suggestions()

    # What the background image jobs do: `.update()`, which sends no signal.
    CategoryProfile.objects.filter(category=category).update(**picture("a"))
    Collection.objects.filter(pk=collection.pk).update(**picture("b"))
    CampaignPage.objects.filter(pk=page.pk).update(**picture("c"))
    Brand.objects.filter(pk=brand.pk).update(
        logo_status="ready",
        logo_derivatives=[{"width": 400, "name": "x/d-400.webp"}],
    )
    tasks.refresh_suggestions()

    shown = {
        g["type"]: g["items"][0]["image_url"]
        for g in groups(client, "nar")
        if g["type"] != "product"  # the Product is found by its Brand
    }
    assert shown == {
        kind: f"http://media.testserver/x/{name}-400.webp"
        for kind, name in (
            ("category", "a"),
            ("collection", "b"),
            ("campaign_page", "c"),
            ("brand", "d"),
        )
    }


def test_a_change_of_collection_membership_shows_up_after_one_refresh(client):
    product = make("Keten Elbise")
    collection = make_collection("Yazlık Seçki")
    tasks.refresh_suggestions()
    assert names(client, "secki", "collection") == []

    CollectionProduct.objects.bulk_create(
        [CollectionProduct(collection=collection, product=product)],
    )
    tasks.refresh_suggestions()
    assert names(client, "secki", "collection") == ["Yazlık Seçki"]

    CollectionProduct.objects.filter(collection=collection).delete()
    tasks.refresh_suggestions()
    assert names(client, "secki", "collection") == []


def test_a_campaign_page_comes_and_goes_with_its_dates_alone(client, monkeypatch):
    make_page(starts_at=day(1), ends_at=day(3))
    real_now = timezone.now
    shown = []
    for days_later in (0, 2, 4):  # nothing is written in between
        later = timedelta(days=days_later)
        monkeypatch.setattr(timezone, "now", lambda later=later: real_now() + later)
        tasks.refresh_suggestions()
        shown.append(names(client, "bahar", "campaign_page"))

    assert shown == [[], ["Bahar İndirimi"], []]


# --- an index from an earlier release --------------------------------------------


@pytest.fixture
def old_index(settings):
    """An index as an earlier release left it: its alias, and strict mappings without
    today's fields."""
    alias = settings.SUGGESTIONS_INDEX
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        es.indices.create(
            index=f"{alias}-old",
            aliases={alias: {}},
            mappings={"dynamic": "strict", "properties": {"name": {"type": "text"}}},
        )
    return f"{alias}-old"


def test_before_its_first_refresh_an_older_index_counts_as_not_indexed(
    client,
    old_index,
):
    response = client.get(URL, {"q": "keten"})

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_the_refresh_replaces_an_index_with_an_older_format(
    client,
    settings,
    old_index,
):
    make("Keten Elbise")

    assert tasks.refresh_suggestions() > 0

    assert names(client, "keten") == ["Keten Elbise"]
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        assert old_index not in es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*")


def test_the_refresh_updates_a_current_index_in_place(client, settings):
    make("Keten Elbise")
    tasks.refresh_suggestions()
    with Elasticsearch(settings.ELASTICSEARCH_URL) as es:
        before = list(es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*"))

        tasks.refresh_suggestions()

        assert list(es.indices.get(index=f"{settings.SUGGESTIONS_INDEX}-*")) == before
