# ruff: noqa: PLR2004
"""The Owner's Campaign management over the admin API (GLOSSARY: Campaign)."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Campaign
from store_backend.catalog.models import Collection
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import campaign_admin
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

CAMPAIGNS = "/api/admin/campaigns"


@pytest.fixture
def boss(owner, api_for):  # noqa: F811
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


def make_product(title="Elbise", *, category=None, cost_price=None, shelf="100.00"):
    return catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=category or catalog.create_category(f"{title} kategorisi"),
        shelf_price=shelf,
        cost_price=cost_price,
    )


def create(boss, **fields):
    body = {"name": "Kara Cuma", "percentage": "20", **fields}
    response = boss.post(CAMPAIGNS, body)
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def audited(action):
    return AuditEntry.objects.filter(action=action).count()


# --- who may use it ------------------------------------------------------------------


def test_staff_cannot_touch_campaigns(boss, staff):
    made = create(boss, product_ids=[make_product().pk])
    item = f"{CAMPAIGNS}/{made['id']}"

    refused = [
        staff.get(CAMPAIGNS),
        staff.get(item),
        staff.post(CAMPAIGNS, {"name": "x", "percentage": "10", "product_ids": [1]}),
        staff.patch(item, {"name": "y"}),
        staff.post(f"{item}/activate"),
        staff.post(f"{item}/deactivate"),
        staff.delete(item),
    ]

    assert {r.status_code for r in refused} == {HTTPStatus.FORBIDDEN}
    assert Campaign.objects.get().name == "Kara Cuma"


# --- create --------------------------------------------------------------------------


def test_the_owner_creates_a_campaign_on_products_categories_and_collections(boss):
    product = make_product("Elbise")
    other = make_product("Etek")
    collection = Collection.objects.create(name="Sonbahar", slug="sonbahar")

    made = create(
        boss,
        product_ids=[product.pk],
        category_ids=[other.categories.get().pk],
        collection_ids=[collection.pk],
        include_subcategories=True,
    )

    assert made["percentage"] == "20.00"
    assert (made["is_active"], made["status"]) == (True, "active")
    assert made["include_subcategories"] is True
    assert made["products"] == [{"id": product.pk, "name": "Elbise"}]
    assert made["collections"] == [{"id": collection.pk, "name": "Sonbahar"}]
    assert len(made["categories"]) == 1
    assert (made["starts_at"], made["ends_at"]) == (None, None)  # open-ended
    assert audited("campaign_created") == 1


@pytest.mark.parametrize("percentage", ["0", "0.5", "100", "-5", "150"])
def test_the_percentage_must_be_1_to_99(boss, percentage):
    response = boss.post(
        CAMPAIGNS,
        {"name": "x", "percentage": percentage, "product_ids": [make_product().pk]},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not Campaign.objects.exists()


def test_a_campaign_needs_a_target(boss):
    response = boss.post(CAMPAIGNS, {"name": "x", "percentage": "10"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_an_unknown_target_is_refused(boss):
    response = boss.post(
        CAMPAIGNS,
        {"name": "x", "percentage": "10", "product_ids": [999999]},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not Campaign.objects.exists()


def test_the_end_must_come_after_the_start(boss):
    now = timezone.now()

    response = boss.post(
        CAMPAIGNS,
        {
            "name": "x",
            "percentage": "10",
            "product_ids": [make_product().pk],
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_the_name_may_not_be_blank(boss):
    response = boss.post(
        CAMPAIGNS,
        {"name": "  ", "percentage": "10", "product_ids": [make_product().pk]},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- status --------------------------------------------------------------------------


def test_the_status_follows_the_schedule_and_the_switch(boss):
    now = timezone.now()
    product = [make_product().pk]
    future = create(
        boss,
        product_ids=product,
        starts_at=(now + timedelta(days=1)).isoformat(),
    )
    past = create(
        boss,
        product_ids=product,
        starts_at=(now - timedelta(days=2)).isoformat(),
        ends_at=(now - timedelta(days=1)).isoformat(),
    )
    running = create(
        boss,
        product_ids=product,
        ends_at=(now + timedelta(days=1)).isoformat(),
    )
    off = create(boss, product_ids=product, is_active=False)

    assert future["status"] == "scheduled"
    assert past["status"] == "ended"
    assert running["status"] == "active"
    assert off["status"] == "inactive"
    for wanted, expected in (
        ("scheduled", future),
        ("ended", past),
        ("active", running),
        ("inactive", off),
    ):
        listed = boss.get(CAMPAIGNS, data={"status": wanted}).json()
        assert [row["id"] for row in listed["items"]] == [expected["id"]], wanted


def test_the_switch_beats_the_schedule(boss):
    made = create(boss, product_ids=[make_product().pk])

    off = boss.post(f"{CAMPAIGNS}/{made['id']}/deactivate").json()
    on = boss.post(f"{CAMPAIGNS}/{made['id']}/activate").json()

    assert (off["is_active"], off["status"]) == (False, "inactive")
    assert (on["is_active"], on["status"]) == (True, "active")
    assert audited("campaign_deactivated") == 1
    assert audited("campaign_activated") == 1


def test_switching_to_the_state_it_is_in_is_not_audited(boss):
    made = create(boss, product_ids=[make_product().pk])

    boss.post(f"{CAMPAIGNS}/{made['id']}/activate")

    assert audited("campaign_activated") == 0


def test_an_unknown_status_filter_is_refused(boss):
    refused = boss.get(CAMPAIGNS, data={"status": "nope"})

    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- list and get --------------------------------------------------------------------


def test_the_list_is_paged_newest_first(boss):
    product = [make_product().pk]
    for n in range(3):
        create(boss, name=f"Kampanya {n}", product_ids=product)

    page = boss.get(CAMPAIGNS, data={"page_size": 2}).json()

    assert page["total"] == 3
    assert [row["name"] for row in page["items"]] == ["Kampanya 2", "Kampanya 1"]


def test_get_one_campaign(boss):
    made = create(boss, product_ids=[make_product().pk])

    got = boss.get(f"{CAMPAIGNS}/{made['id']}")

    assert got.status_code == HTTPStatus.OK
    assert got.json()["id"] == made["id"]
    assert boss.get(f"{CAMPAIGNS}/999999").status_code == HTTPStatus.NOT_FOUND


# --- edit ----------------------------------------------------------------------------


def test_editing_changes_only_what_was_sent_and_is_audited_with_before_and_after(boss):
    keep = make_product("Elbise")
    made = create(boss, product_ids=[keep.pk])

    edited = boss.patch(
        f"{CAMPAIGNS}/{made['id']}",
        {"name": "Yeni ad", "percentage": "35"},
    )

    assert edited.status_code == HTTPStatus.OK, edited.content
    body = edited.json()
    assert (body["name"], body["percentage"]) == ("Yeni ad", "35.00")
    assert [p["id"] for p in body["products"]] == [keep.pk]
    entry = AuditEntry.objects.get(action="campaign_edited")
    assert entry.before["name"] == "Kara Cuma"
    assert entry.after["name"] == "Yeni ad"
    assert entry.after["percentage"] == "35.00"


def test_editing_replaces_the_targets_that_were_sent(boss):
    first = make_product("Elbise")
    second = make_product("Etek")
    made = create(boss, product_ids=[first.pk])

    edited = boss.patch(f"{CAMPAIGNS}/{made['id']}", {"product_ids": [second.pk]})

    assert [p["id"] for p in edited.json()["products"]] == [second.pk]


def test_editing_cannot_leave_a_campaign_without_targets(boss):
    made = create(boss, product_ids=[make_product().pk])

    response = boss.patch(f"{CAMPAIGNS}/{made['id']}", {"product_ids": []})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert Campaign.objects.get().products.count() == 1


def test_the_schedule_can_be_cleared_back_to_open_ended(boss):
    now = timezone.now()
    made = create(
        boss,
        product_ids=[make_product().pk],
        ends_at=(now + timedelta(days=1)).isoformat(),
    )

    edited = boss.patch(f"{CAMPAIGNS}/{made['id']}", {"ends_at": None})

    assert edited.json()["ends_at"] is None


def test_an_edit_that_breaks_the_schedule_is_refused(boss):
    now = timezone.now()
    made = create(
        boss,
        product_ids=[make_product().pk],
        starts_at=now.isoformat(),
        ends_at=(now + timedelta(days=1)).isoformat(),
    )

    response = boss.patch(
        f"{CAMPAIGNS}/{made['id']}",
        {"ends_at": (now - timedelta(days=1)).isoformat()},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- what a Campaign reaches ---------------------------------------------------------


def test_affected_products_come_from_products_categories_and_collections(boss):
    direct = make_product("Direkt")
    in_category = make_product("Kategoride")
    in_collection = make_product("Koleksiyonda")
    untouched = make_product("Dokunulmamış")
    collection = Collection.objects.create(name="Sonbahar", slug="sonbahar")
    collection.places.create(product=in_collection, position=0)

    made = create(
        boss,
        product_ids=[direct.pk],
        category_ids=[in_category.categories.get().pk],
        collection_ids=[collection.pk],
    )

    campaign = Campaign.objects.get(pk=made["id"])
    assert made["affected_products"] == 3
    assert campaign_admin.affected_product_ids(campaign) == {
        direct.pk,
        in_category.pk,
        in_collection.pk,
    }
    assert untouched.pk not in campaign_admin.affected_product_ids(campaign)


def test_subcategories_count_only_when_flagged(boss):
    parent = catalog.create_category("Giyim")
    child = catalog.create_category("Elbise", parent)
    product = make_product("Midi", category=child)

    plain = create(boss, category_ids=[parent.pk])
    deep = create(boss, category_ids=[parent.pk], include_subcategories=True)

    assert plain["affected_products"] == 0
    assert deep["affected_products"] == 1
    assert product.pk in campaign_admin.affected_product_ids(
        Campaign.objects.get(pk=deep["id"]),
    )


# --- below cost ----------------------------------------------------------------------


def test_the_form_is_warned_about_products_pushed_below_cost_but_may_still_save(boss):
    cheap = make_product("Zararına", cost_price="90.00", shelf="100.00")
    fine = make_product("Kârlı", cost_price="50.00", shelf="100.00")
    no_cost = make_product("Maliyetsiz")

    made = create(
        boss,
        percentage="20",
        product_ids=[cheap.pk, fine.pk, no_cost.pk],
    )

    assert made["below_cost"] == [
        {
            "product_id": cheap.pk,
            "title": "Zararına",
            "campaign_price": "80.00",
            "cost_price": "90.00",
        },
    ]
    assert Campaign.objects.count() == 1


def test_a_variant_override_can_be_the_one_below_cost(boss):
    product = make_product("Elbise", cost_price="70.00", shelf="200.00")
    catalog.create_variant(
        product,
        colour=Colour.objects.create(name="Kırmızı", code="KIR", hex_code="#ff0000"),
        size="M",
        price_override="80.00",
    )

    made = create(boss, percentage="20", product_ids=[product.pk])

    assert [row["campaign_price"] for row in made["below_cost"]] == ["64.00"]


# --- delete --------------------------------------------------------------------------


def test_the_owner_deletes_an_unused_campaign_and_it_is_audited(boss):
    made = create(boss, product_ids=[make_product().pk])

    response = boss.delete(f"{CAMPAIGNS}/{made['id']}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert not Campaign.objects.exists()
    entry = AuditEntry.objects.get(action="campaign_deleted")
    assert entry.before["name"] == "Kara Cuma"


def test_a_campaign_an_order_used_cannot_be_deleted_only_deactivated(
    boss,
    monkeypatch,
):
    made = create(boss, product_ids=[make_product().pk])
    monkeypatch.setattr(campaign_admin, "orders_using", lambda campaign: 2)

    refused = boss.delete(f"{CAMPAIGNS}/{made['id']}")
    off = boss.post(f"{CAMPAIGNS}/{made['id']}/deactivate")

    assert refused.status_code == HTTPStatus.CONFLICT
    assert Campaign.objects.count() == 1
    assert audited("campaign_deleted") == 0
    assert off.status_code == HTTPStatus.OK


def test_deleting_a_campaign_leaves_its_targets_alone(boss):
    product = make_product()
    collection = Collection.objects.create(name="Sonbahar", slug="sonbahar")
    made = create(boss, product_ids=[product.pk], collection_ids=[collection.pk])

    boss.delete(f"{CAMPAIGNS}/{made['id']}")

    assert Collection.objects.filter(pk=collection.pk).exists()
    assert ProductProfile.objects.filter(product=product).exists()
    assert ProductProfile.objects.get(product=product).status == ProductStatus.DRAFT
