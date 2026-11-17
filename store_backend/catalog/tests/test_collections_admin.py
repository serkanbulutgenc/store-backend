# ruff: noqa: PLR2004
"""The Owner's Collection management over the admin API (GLOSSARY: Collection)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

COLLECTIONS = "/api/admin/collections"


@pytest.fixture
def boss(owner, api_for):  # noqa: F811
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


def make_product(title="Elbise", *, status=ProductStatus.PUBLISHED):
    made = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
    )
    ProductProfile.objects.filter(product=made).update(status=status)
    return made


def create(boss, **fields):
    response = boss.post(COLLECTIONS, {"name": "Editörün seçimi", **fields})
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def audited(action):
    return AuditEntry.objects.filter(action=action).count()


# --- who may use it ------------------------------------------------------------------


def test_staff_cannot_use_collections_yet(staff, boss):
    made = create(boss)
    assert staff.get(COLLECTIONS).status_code == HTTPStatus.FORBIDDEN
    assert staff.post(COLLECTIONS, {"name": "X"}).status_code == HTTPStatus.FORBIDDEN
    for action in ("publish", "unpublish"):
        url = f"{COLLECTIONS}/{made['id']}/{action}"
        assert staff.post(url).status_code == HTTPStatus.FORBIDDEN
    assert staff.delete(f"{COLLECTIONS}/{made['id']}").status_code == (
        HTTPStatus.FORBIDDEN
    )
    assert Collection.objects.count() == 1


# --- create and read -----------------------------------------------------------------


def test_the_owner_creates_a_draft_with_a_slug_made_from_the_name(boss):
    made = create(boss, name="Sonbahar Koleksiyonu İçin", description="Yeni sezon")

    assert made["status"] == "draft"
    assert made["slug"] == "sonbahar-koleksiyonu-icin"
    assert made["description"] == "Yeni sezon"
    assert made["published_at"] is None
    assert made["products"] == []
    assert audited("collection_created") == 1


def test_a_taken_slug_gets_a_number_when_made_and_is_refused_when_chosen(boss):
    create(boss, name="Yaz")
    assert create(boss, name="Yaz")["slug"] == "yaz-2"

    clash = boss.post(COLLECTIONS, {"name": "Başka", "slug": "yaz"})
    assert clash.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "slug" in clash.json()["fields"]


def test_a_collection_needs_a_name(boss):
    blank = boss.post(COLLECTIONS, {"name": "   "})
    assert blank.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "name" in blank.json()["fields"]


def test_products_keep_the_order_they_were_given_in(boss):
    a, b, c = (make_product(t) for t in ("A", "B", "C"))
    made = create(boss, product_ids=[c.pk, a.pk, b.pk])

    assert [p["id"] for p in made["products"]] == [c.pk, a.pk, b.pk]
    again = boss.get(f"{COLLECTIONS}/{made['id']}").json()
    assert [p["title"] for p in again["products"]] == ["C", "A", "B"]


def test_unknown_and_repeated_products_are_refused(boss):
    a = make_product("A")
    missing = boss.post(COLLECTIONS, {"name": "X", "product_ids": [a.pk, 999999]})
    repeated = boss.post(COLLECTIONS, {"name": "Y", "product_ids": [a.pk, a.pk]})

    for response in (missing, repeated):
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "product_ids" in response.json()["fields"]
    assert not Collection.objects.exists()


def test_draft_and_archived_products_may_be_picked_but_are_not_counted_visible(boss):
    live = make_product("Canlı")
    draft = make_product("Taslak", status=ProductStatus.DRAFT)
    old = make_product("Eski", status=ProductStatus.ARCHIVED)

    made = create(boss, product_ids=[live.pk, draft.pk, old.pk])

    assert len(made["products"]) == 3
    assert {p["title"]: p["status"] for p in made["products"]} == {
        "Canlı": "published",
        "Taslak": "draft",
        "Eski": "archived",
    }
    assert made["visible_product_count"] == 1


def test_the_list_can_be_filtered_by_status(boss):
    first = create(boss, name="Birinci")
    create(boss, name="İkinci")
    boss.post(f"{COLLECTIONS}/{first['id']}/publish")

    everything = boss.get(COLLECTIONS).json()
    published = boss.get(f"{COLLECTIONS}?status=published").json()

    assert [c["name"] for c in everything] == ["Birinci", "İkinci"]
    assert [c["name"] for c in published] == ["Birinci"]
    assert "products" not in everything[0]
    assert everything[0]["product_count"] == 0


def test_an_unknown_collection_is_not_found(boss):
    assert boss.get(f"{COLLECTIONS}/999").status_code == HTTPStatus.NOT_FOUND


# --- edit ----------------------------------------------------------------------------


def test_the_owner_edits_copy_and_reorders_products(boss):
    a, b = make_product("A"), make_product("B")
    made = create(boss, product_ids=[a.pk, b.pk])

    edited = boss.patch(
        f"{COLLECTIONS}/{made['id']}",
        {"name": "Yeni ad", "description": "Not", "product_ids": [b.pk]},
    )

    assert edited.status_code == HTTPStatus.OK, edited.content
    body = edited.json()
    assert (body["name"], body["description"]) == ("Yeni ad", "Not")
    assert [p["id"] for p in body["products"]] == [b.pk]
    assert body["slug"] == made["slug"]
    assert CollectionProduct.objects.count() == 1
    assert audited("collection_edited") == 1


def test_an_edit_that_leaves_fields_out_keeps_them(boss):
    a = make_product("A")
    made = create(boss, description="Kalsın", product_ids=[a.pk])

    boss.patch(f"{COLLECTIONS}/{made['id']}", {"name": "Başka"})

    kept = boss.get(f"{COLLECTIONS}/{made['id']}").json()
    assert kept["description"] == "Kalsın"
    assert [p["id"] for p in kept["products"]] == [a.pk]


def test_a_failed_edit_changes_nothing(boss):
    a = make_product("A")
    made = create(boss, product_ids=[a.pk])

    bad = boss.patch(
        f"{COLLECTIONS}/{made['id']}",
        {"name": "Yeni", "product_ids": [999999]},
    )

    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    kept = boss.get(f"{COLLECTIONS}/{made['id']}").json()
    assert kept["name"] == made["name"]
    assert [p["id"] for p in kept["products"]] == [a.pk]


# --- publish and unpublish -----------------------------------------------------------


def test_the_owner_publishes_a_draft_then_unpublishes_and_republishes_it(boss):
    made = create(boss)
    url = f"{COLLECTIONS}/{made['id']}"

    published = boss.post(f"{url}/publish").json()
    assert published["status"] == "published"
    assert published["published_at"] is not None

    assert boss.post(f"{url}/unpublish").json()["status"] == "unpublished"
    assert boss.post(f"{url}/publish").json()["status"] == "published"
    assert audited("collection_published") == 2
    assert audited("collection_unpublished") == 1


def test_publishing_and_unpublishing_need_the_right_starting_state(boss):
    made = create(boss)
    url = f"{COLLECTIONS}/{made['id']}"

    assert boss.post(f"{url}/unpublish").status_code == HTTPStatus.CONFLICT
    boss.post(f"{url}/publish")
    assert boss.post(f"{url}/publish").status_code == HTTPStatus.CONFLICT
    assert audited("collection_published") == 1


def test_publishing_an_empty_collection_is_allowed(boss):
    made = create(boss)
    response = boss.post(f"{COLLECTIONS}/{made['id']}/publish")
    assert response.status_code == HTTPStatus.OK
    assert response.json()["visible_product_count"] == 0


# --- delete --------------------------------------------------------------------------


def test_deleting_a_collection_leaves_its_products_alone(boss):
    a = make_product("A")
    made = create(boss, product_ids=[a.pk])
    boss.post(f"{COLLECTIONS}/{made['id']}/publish")

    response = boss.delete(f"{COLLECTIONS}/{made['id']}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert not Collection.objects.exists()
    assert not CollectionProduct.objects.exists()
    assert Product.objects.filter(pk=a.pk).exists()
    entry = AuditEntry.objects.get(action="collection_deleted")
    assert "Editörün seçimi" in entry.summary


def test_deleting_a_product_drops_it_from_its_collections(boss):
    a, b = make_product("A"), make_product("B")
    made = create(boss, product_ids=[a.pk, b.pk])

    a.delete()

    kept = boss.get(f"{COLLECTIONS}/{made['id']}").json()
    assert [p["id"] for p in kept["products"]] == [b.pk]
