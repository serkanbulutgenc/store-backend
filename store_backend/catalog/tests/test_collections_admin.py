# ruff: noqa: PLR2004
"""The Owner's Collection management over the admin API (GLOSSARY: Collection)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Collection
from store_backend.catalog.models import CollectionChange
from store_backend.catalog.models import CollectionProduct
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_member
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


def staff_draft(staff, **fields):
    response = staff.post(COLLECTIONS, {"name": "Personel taslağı", **fields})
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def action(client, made, name, data=None):
    return client.post(f"{COLLECTIONS}/{made['id']}/{name}", data)


# --- who may use it ------------------------------------------------------------------


def test_staff_cannot_publish_unpublish_approve_reject_or_delete(staff, boss):
    made = create(boss)
    base = f"{COLLECTIONS}/{made['id']}"
    for action in ("publish", "unpublish", "approve", "reject"):
        assert staff.post(f"{base}/{action}").status_code == HTTPStatus.FORBIDDEN
    assert staff.delete(base).status_code == HTTPStatus.FORBIDDEN
    assert Collection.objects.get().status == "draft"


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


# --- Staff drafts and the approval flow ----------------------------------------------


def test_staff_create_edit_and_list_drafts_including_other_peoples(staff, boss):
    mine = staff_draft(staff)
    theirs = create(boss, name="Sahibin taslağı")

    edited = staff.patch(f"{COLLECTIONS}/{theirs['id']}", {"name": "Düzeltildi"})
    assert edited.status_code == HTTPStatus.OK
    assert edited.json()["name"] == "Düzeltildi"
    assert (
        staff.patch(f"{COLLECTIONS}/{mine['id']}", {"description": "x"}).status_code
        == HTTPStatus.OK
    )
    listed = staff.get(COLLECTIONS)
    assert {row["id"] for row in listed.json()} == {mine["id"], theirs["id"]}
    assert staff.get(f"{COLLECTIONS}/{mine['id']}").status_code == HTTPStatus.OK


def test_staff_submit_a_draft_and_it_waits_for_the_owner(
    staff,
    boss,
    staff_user,  # noqa: F811
):
    made = staff_draft(staff)

    submitted = action(staff, made, "submit")

    assert submitted.status_code == HTTPStatus.OK
    body = submitted.json()
    assert body["status"] == "pending_approval"
    assert body["submitted_by"]["id"] == staff_user.pk
    assert body["submitted_at"] is not None
    assert audited("collection_submitted") == 1
    waiting = boss.get(f"{COLLECTIONS}?status=pending_approval").json()
    assert [row["id"] for row in waiting] == [made["id"]]


def test_only_a_draft_can_be_submitted(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")
    assert action(staff, made, "submit").status_code == HTTPStatus.CONFLICT

    other = create(boss)
    action(boss, other, "publish")
    assert action(staff, other, "submit").status_code == HTTPStatus.CONFLICT
    action(boss, other, "unpublish")
    assert action(staff, other, "submit").status_code == HTTPStatus.CONFLICT
    assert audited("collection_submitted") == 1


def test_staff_cannot_edit_a_pending_or_unpublished_collection(staff, boss):
    pending = staff_draft(staff, name="Bekleyen")
    action(staff, pending, "submit")
    live = create(boss, name="Yayında")
    action(boss, live, "publish")
    off = create(boss, name="Kapalı")
    action(boss, off, "publish")
    action(boss, off, "unpublish")

    for made in (pending, off):
        response = staff.patch(f"{COLLECTIONS}/{made['id']}", {"name": "Değişti"})
        assert response.status_code in {HTTPStatus.CONFLICT, HTTPStatus.FORBIDDEN}
    assert not Collection.objects.filter(name="Değişti").exists()
    assert Collection.objects.get(name="Yayında").status == "published"
    assert not CollectionChange.objects.exists()


def test_staff_withdraw_their_own_submission_back_to_draft(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")

    withdrawn = action(staff, made, "withdraw")

    assert withdrawn.status_code == HTTPStatus.OK
    assert withdrawn.json()["status"] == "draft"
    assert audited("collection_withdrawn") == 1
    assert staff.patch(f"{COLLECTIONS}/{made['id']}", {"name": "Yeni"}).status_code == (
        HTTPStatus.OK
    )


def test_staff_cannot_withdraw_someone_elses_submission_or_a_draft(
    staff,
    boss,
    api_for,  # noqa: F811
):
    made = staff_draft(staff)
    assert action(staff, made, "withdraw").status_code == HTTPStatus.CONFLICT
    action(staff, made, "submit")

    colleague = api_for(staff_member("Staff"))
    assert action(colleague, made, "withdraw").status_code == HTTPStatus.FORBIDDEN
    assert Collection.objects.get().status == "pending_approval"


def test_the_owner_approves_a_pending_collection_which_publishes_it(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")

    approved = action(boss, made, "approve")

    assert approved.status_code == HTTPStatus.OK
    assert approved.json()["status"] == "published"
    assert approved.json()["published_at"] is not None
    assert audited("collection_approved") == 1


def test_the_owner_edits_a_pending_collection_and_approval_publishes_the_edit(
    staff,
    boss,
):
    a = make_product("A")
    made = staff_draft(staff)
    action(staff, made, "submit")

    edited = boss.patch(
        f"{COLLECTIONS}/{made['id']}",
        {"name": "Sahibin düzeltmesi", "product_ids": [a.pk]},
    )
    assert edited.json()["status"] == "pending_approval"
    approved = action(boss, made, "approve").json()

    assert approved["name"] == "Sahibin düzeltmesi"
    assert [p["id"] for p in approved["products"]] == [a.pk]


def test_the_owner_rejects_with_a_reason_the_submitter_can_read(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")

    rejected = action(boss, made, "reject", {"reason": "Kapak görseli eksik"})

    assert rejected.status_code == HTTPStatus.OK
    assert rejected.json()["status"] == "draft"
    seen = staff.get(f"{COLLECTIONS}/{made['id']}").json()
    assert seen["rejection_reason"] == "Kapak görseli eksik"
    assert audited("collection_rejected") == 1
    entry = AuditEntry.objects.get(action="collection_rejected")
    assert entry.after["reason"] == "Kapak görseli eksik"


def test_a_rejection_reason_is_optional_and_clears_on_resubmission(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")
    assert action(boss, made, "reject").json()["rejection_reason"] == ""

    action(staff, made, "submit")
    action(boss, made, "reject", {"reason": "Olmadı"})
    assert staff.get(f"{COLLECTIONS}/{made['id']}").json()["rejection_reason"]
    resubmitted = action(staff, made, "submit").json()
    assert resubmitted["rejection_reason"] == ""


def test_approve_and_reject_only_apply_to_pending_collections(boss, staff):
    made = staff_draft(staff)
    assert action(boss, made, "approve").status_code == HTTPStatus.CONFLICT
    assert action(boss, made, "reject").status_code == HTTPStatus.CONFLICT
    action(boss, made, "publish")
    assert action(boss, made, "approve").status_code == HTTPStatus.CONFLICT
    assert audited("collection_approved") == 0


def test_a_pending_collection_cannot_be_published_unpublished_around_approval(
    staff,
    boss,
):
    made = staff_draft(staff)
    action(staff, made, "submit")
    assert action(boss, made, "publish").status_code == HTTPStatus.CONFLICT
    assert action(boss, made, "unpublish").status_code == HTTPStatus.CONFLICT


def test_an_overlong_rejection_reason_is_refused(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")
    response = action(boss, made, "reject", {"reason": "x" * 501})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert Collection.objects.get().status == "pending_approval"


def test_the_owner_can_delete_a_pending_collection(staff, boss):
    made = staff_draft(staff)
    action(staff, made, "submit")
    assert boss.delete(f"{COLLECTIONS}/{made['id']}").status_code == (
        HTTPStatus.NO_CONTENT
    )


# --- pending changes on published Collections ----------------------------------------


@pytest.fixture
def live(boss):
    a, b = make_product("A"), make_product("B")
    made = create(boss, name="Yayında", description="Eski", product_ids=[a.pk])
    action(boss, made, "publish")
    made["a"], made["b"] = a.pk, b.pk
    return made


def edit_live(client, made, **fields):
    return client.patch(f"{COLLECTIONS}/{made['id']}", fields)


def test_a_staff_edit_of_a_published_collection_is_held_as_a_pending_change(
    staff,
    boss,
    live,
    staff_user,  # noqa: F811
):
    response = edit_live(staff, live, name="Yeni ad", product_ids=[live["b"]])

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["name"] == "Yayında"  # the live version is unchanged
    assert [p["id"] for p in body["products"]] == [live["a"]]
    change = body["pending_change"]
    assert change["name"] == "Yeni ad"
    assert change["description"] == "Eski"  # untouched fields carry over
    assert [p["id"] for p in change["products"]] == [live["b"]]
    assert change["submitted_by"]["id"] == staff_user.pk
    assert body["status"] == "published"
    assert audited("collection_change_submitted") == 1
    row = boss.get(COLLECTIONS).json()[0]
    assert row["has_pending_change"] is True


def test_a_second_staff_edit_amends_the_one_pending_change(staff, live):
    edit_live(staff, live, name="Birinci")
    second = edit_live(staff, live, description="İkinci").json()

    assert second["pending_change"]["name"] == "Birinci"
    assert second["pending_change"]["description"] == "İkinci"
    assert CollectionChange.objects.count() == 1
    assert audited("collection_change_submitted") == 2


def test_a_staff_edit_that_is_invalid_stages_nothing(staff, live):
    response = edit_live(staff, live, name="  ")
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not CollectionChange.objects.exists()


def test_the_owner_approves_a_pending_change_which_goes_live(staff, boss, live):
    edit_live(staff, live, name="Yeni ad", product_ids=[live["b"], live["a"]])

    approved = action(boss, live, "approve")

    assert approved.status_code == HTTPStatus.OK
    body = approved.json()
    assert body["name"] == "Yeni ad"
    assert [p["id"] for p in body["products"]] == [live["b"], live["a"]]
    assert body["pending_change"] is None
    assert body["status"] == "published"
    assert not CollectionChange.objects.exists()
    assert audited("collection_change_approved") == 1


def test_the_owner_rejects_a_pending_change_and_the_live_version_stays(
    staff,
    boss,
    live,
):
    edit_live(staff, live, name="Yeni ad")

    rejected = action(boss, live, "reject", {"reason": "Ad uygun değil"})

    assert rejected.status_code == HTTPStatus.OK
    body = rejected.json()
    assert body["name"] == "Yayında"
    assert body["status"] == "published"
    assert body["pending_change"] is None
    assert not CollectionChange.objects.exists()
    assert audited("collection_change_rejected") == 1
    seen = staff.get(f"{COLLECTIONS}/{live['id']}").json()
    assert seen["rejection_reason"] == "Ad uygun değil"


def test_a_new_staff_edit_clears_the_old_rejection_reason(staff, boss, live):
    edit_live(staff, live, name="Bir")
    action(boss, live, "reject", {"reason": "Olmadı"})

    edited = edit_live(staff, live, name="İki").json()

    assert edited["rejection_reason"] == ""


def test_the_owner_cannot_edit_the_live_collection_while_a_change_is_pending(
    staff,
    boss,
    live,
):
    edit_live(staff, live, name="Yeni ad")

    response = edit_live(boss, live, name="Sahibin adı")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "collection_has_pending_change"
    assert Collection.objects.get().name == "Yayında"
    # after approving or rejecting, the Owner edits directly again
    action(boss, live, "reject")
    assert edit_live(boss, live, name="Sahibin adı").status_code == HTTPStatus.OK


def test_the_owner_cannot_unpublish_while_a_change_is_pending(staff, boss, live):
    edit_live(staff, live, name="Yeni ad")
    response = action(boss, live, "unpublish")
    assert response.status_code == HTTPStatus.CONFLICT
    assert Collection.objects.get().status == "published"


def test_the_owner_edits_a_pending_change_before_approving_it(staff, boss, live):
    edit_live(staff, live, name="Personel adı")

    response = boss.patch(
        f"{COLLECTIONS}/{live['id']}/pending-change",
        {"description": "Sahibin açıklaması"},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json()["pending_change"]["description"] == "Sahibin açıklaması"
    approved = action(boss, live, "approve").json()
    assert approved["name"] == "Personel adı"
    assert approved["description"] == "Sahibin açıklaması"


def test_editing_a_pending_change_needs_one_and_the_owner(staff, boss, live):
    url = f"{COLLECTIONS}/{live['id']}/pending-change"
    assert boss.patch(url, {"name": "X"}).status_code == HTTPStatus.CONFLICT
    edit_live(staff, live, name="Y")
    assert staff.patch(url, {"name": "X"}).status_code == HTTPStatus.FORBIDDEN


def test_approval_refuses_a_change_whose_slug_was_taken_meanwhile(staff, boss, live):
    edit_live(staff, live, slug="ortak-adres")
    create(boss, name="Başkası", slug="ortak-adres")

    response = action(boss, live, "approve")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert Collection.objects.get(pk=live["id"]).slug != "ortak-adres"
    assert CollectionChange.objects.count() == 1


def test_a_staff_edit_of_an_unpublished_collection_is_still_refused(staff, boss, live):
    action(boss, live, "unpublish")
    assert edit_live(staff, live, name="X").status_code == HTTPStatus.FORBIDDEN
    assert not CollectionChange.objects.exists()


def test_deleting_a_collection_drops_its_pending_change(staff, boss, live):
    edit_live(staff, live, name="Yeni ad")
    assert boss.delete(f"{COLLECTIONS}/{live['id']}").status_code == (
        HTTPStatus.NO_CONTENT
    )
    assert not CollectionChange.objects.exists()
