# ruff: noqa: PLR2004
"""The Owner's Voucher management over the admin API."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus

import pytest
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import Condition
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.offer.models import Range
from oscar.apps.voucher.models import Voucher

from store_backend.services import audit
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import redeem
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_voucher_checkout import open_basket
from store_backend.shop.tests.test_voucher_checkout import place

pytestmark = pytest.mark.django_db

VOUCHERS = "/api/admin/vouchers"
APPLY = "/api/store/basket/voucher"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


def test_only_the_owner_manages_vouchers(boss, staff):
    assert boss.get(VOUCHERS).status_code == HTTPStatus.OK
    refused = staff.get(VOUCHERS)
    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_the_owner_creates_a_percentage_voucher_the_storefront_can_use(boss, shop):
    response = boss.post(
        VOUCHERS,
        {"code": "YAZ10", "kind": "percentage", "value": "10"},
    )

    assert response.status_code == HTTPStatus.CREATED, response.content
    created = response.json()
    assert created["code"] == "YAZ10"
    assert (created["kind"], created["value"]) == ("percentage", "10.00")
    assert created["minimum"] is None
    assert created["ends_at"] is None
    assert created["cap"] is None
    assert created["once_per_customer"] is False
    assert (created["redemptions"], created["status"]) == (0, "active")
    # One voucher with one offer, benefit and condition, as the Storefront expects.
    assert Voucher.objects.count() == 1
    assert ConditionalOffer.objects.count() == 1
    assert Benefit.objects.count() == 1
    assert Condition.objects.count() == 1
    # And a Guest can really use it: 10% of 2 x 100.00.
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    applied = guest.post(APPLY, {"code": "yaz10"})
    assert applied.status_code == HTTPStatus.OK, applied.content
    assert applied.json()["voucher"]["discount"] == "20.00"


def test_a_fixed_voucher_keeps_every_limit_the_owner_sets(boss, shop):
    response = boss.post(
        VOUCHERS,
        {
            "code": "KIS50",
            "kind": "fixed",
            "value": "50",
            "minimum": "250",
            "starts_at": "2026-01-01T00:00:00Z",
            "ends_at": "2999-01-01T00:00:00Z",
            "cap": 100,
            "once_per_customer": True,
        },
    )

    assert response.status_code == HTTPStatus.CREATED, response.content
    created = response.json()
    assert (created["kind"], created["value"], created["minimum"]) == (
        "fixed",
        "50.00",
        "250.00",
    )
    assert (created["cap"], created["once_per_customer"]) == (100, True)
    starts = datetime.fromisoformat(created["starts_at"])
    ends = datetime.fromisoformat(created["ends_at"])
    assert (starts, ends) == (
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2999, 1, 1, tzinfo=UTC),
    )
    # The rules are really stored where the Storefront reads them.
    voucher = Voucher.objects.get()
    offer = voucher.offers.get()
    assert voucher.usage == Voucher.ONCE_PER_CUSTOMER
    assert offer.max_global_applications == 100
    assert offer.condition.value == Decimal("250.00")
    assert offer.benefit.type == Benefit.FIXED
    # 2 x 100.00 is below the 250.00 minimum, so a Guest is refused with the reason.
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    refused = guest.post(APPLY, {"code": "KIS50"})
    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert refused.json()["fields"] == {"voucher_code": ["minimum"]}


def ten_percent(code):
    return {"code": code, "kind": "percentage", "value": "10"}


def test_the_code_is_tidied_up_and_then_has_to_be_plain_capitals_and_digits(boss):
    tidy = boss.post(VOUCHERS, ten_percent("  yaz-10 "))
    assert tidy.status_code == HTTPStatus.CREATED, tidy.content
    assert tidy.json()["code"] == "YAZ-10"

    for bad in ("YAZ 10", "İNDİRİM", "AB", "A" * 21, "   ", "YAZ_10"):
        response = boss.post(VOUCHERS, ten_percent(bad))
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, bad
        assert list(response.json()["fields"]) == ["code"], bad

    assert Voucher.objects.count() == 1  # only the tidy one was stored


def test_a_percentage_is_1_to_100_and_a_fixed_amount_is_more_than_nothing(boss):
    def attempt(kind, value, code="YAZ10"):
        return boss.post(VOUCHERS, {"code": code, "kind": kind, "value": value})

    for kind, value in (
        ("percentage", "0"),
        ("percentage", "100.01"),
        ("percentage", "-5"),
        ("fixed", "0"),
        ("fixed", "-1"),
    ):
        response = attempt(kind, value)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, (kind, value)
        assert "value" in response.json()["fields"], (kind, value)
    assert Voucher.objects.count() == 0

    # The edges that are allowed: 100% and a single kuruş.
    assert attempt("percentage", "100", "TAM100").status_code == HTTPStatus.CREATED
    assert attempt("fixed", "0.01", "KURUS").status_code == HTTPStatus.CREATED


def test_a_limit_out_of_range_is_refused_naming_the_field(boss):
    def attempt(code, **limits):
        return boss.post(VOUCHERS, {**ten_percent(code), **limits})

    refused = (
        ("minimum", {"minimum": "-0.01"}),
        ("cap", {"cap": 0}),
        ("cap", {"cap": -3}),
        (
            "ends_at",
            {"starts_at": "2026-06-01T00:00:00Z", "ends_at": "2026-05-01T00:00:00Z"},
        ),
        (
            "ends_at",  # ending the very moment it starts is not "after"
            {"starts_at": "2026-06-01T00:00:00Z", "ends_at": "2026-06-01T00:00:00Z"},
        ),
    )
    for field, limits in refused:
        response = attempt("LIMIT", **limits)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, limits
        assert list(response.json()["fields"]) == [field], limits
    assert Voucher.objects.count() == 0

    # The edges that are allowed: no minimum at all, a cap of one, one second of life.
    assert attempt("EDGE1", minimum="0", cap=1).status_code == HTTPStatus.CREATED
    one_second = attempt(
        "EDGE2",
        starts_at="2026-06-01T00:00:00Z",
        ends_at="2026-06-01T00:00:01Z",
    )
    assert one_second.status_code == HTTPStatus.CREATED


def test_a_code_already_in_use_is_refused_whatever_its_case(boss):
    first = boss.post(VOUCHERS, {**ten_percent("YAZ10"), "cap": 5})
    assert first.status_code == HTTPStatus.CREATED, first.content

    for again in ("YAZ10", "yaz10", " Yaz10 "):
        response = boss.post(VOUCHERS, {**ten_percent(again), "kind": "fixed"})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, again
        assert list(response.json()["fields"]) == ["code"], again

    # The first voucher is untouched and nothing else was left behind.
    assert Voucher.objects.count() == 1
    assert ConditionalOffer.objects.count() == 1
    assert ConditionalOffer.objects.get().max_global_applications == 5


def test_creating_a_voucher_is_audited_with_what_was_set(boss, owner):
    response = boss.post(
        VOUCHERS,
        {
            "code": "kis50",
            "kind": "fixed",
            "value": "50",
            "minimum": "250",
            "starts_at": "2026-01-01T00:00:00Z",
            "ends_at": "2999-01-01T00:00:00Z",
            "cap": 100,
            "once_per_customer": True,
        },
    )
    assert response.status_code == HTTPStatus.CREATED, response.content
    voucher = Voucher.objects.get()

    entry = audit.AuditEntry.objects.get(action__startswith="voucher_")
    assert entry.action == "voucher_created"
    assert (entry.actor, entry.actor_email) == (owner, owner.email)
    assert (entry.target_type, entry.target_id) == ("voucher.voucher", str(voucher.pk))
    assert "KIS50" in entry.summary
    assert entry.before is None
    assert entry.after == {
        "code": "KIS50",
        "kind": "fixed",
        "value": "50.00",
        "minimum": "250.00",
        "starts_at": "2026-01-01T00:00:00+00:00",
        "ends_at": "2999-01-01T00:00:00+00:00",
        "cap": 100,
        "once_per_customer": True,
    }


def test_a_refused_voucher_leaves_no_audit_entry(boss):
    boss.post(VOUCHERS, {**ten_percent("AB")})

    assert not audit.AuditEntry.objects.filter(action__startswith="voucher_").exists()


def test_the_list_shows_the_newest_voucher_first_with_its_redemptions(boss):
    for code in ("ILK", "ORTA", "SON"):
        assert boss.post(VOUCHERS, ten_percent(code)).status_code == HTTPStatus.CREATED
    for _ in range(2):
        redeem(Voucher.objects.get(code="ILK"))

    response = boss.get(VOUCHERS)

    assert response.status_code == HTTPStatus.OK
    rows = response.json()["items"]
    assert [(r["code"], r["redemptions"]) for r in rows] == [
        ("SON", 0),
        ("ORTA", 0),
        ("ILK", 2),
    ]
    assert rows[0]["kind"] == "percentage"
    assert rows[0]["value"] == "10.00"


def test_a_voucher_has_one_status_and_the_first_that_applies_wins(boss):
    past = {"starts_at": "2020-01-01T00:00:00Z", "ends_at": "2021-01-01T00:00:00Z"}
    future = {"starts_at": "2998-01-01T00:00:00Z", "ends_at": "2999-01-01T00:00:00Z"}
    made = {
        "AKTIF": {},
        "BITMIS": past,
        "BEKLEYEN": future,
        "TUKENMIS": {"cap": 1},
        "KAPALI": {},
        "KAPALI-BITMIS": past,  # suspended and past its end: inactive wins
        "BITMIS-TUKENMIS": {**past, "cap": 1},  # past its end and used up: expired
    }
    for code, limits in made.items():
        response = boss.post(VOUCHERS, {**ten_percent(code), **limits})
        assert response.status_code == HTTPStatus.CREATED, (code, response.content)
    for code in ("TUKENMIS", "BITMIS-TUKENMIS"):
        redeem(Voucher.objects.get(code=code))
    for code in ("KAPALI", "KAPALI-BITMIS"):
        offer = Voucher.objects.get(code=code).offers.get()
        offer.status = ConditionalOffer.SUSPENDED
        offer.save()

    rows = boss.get(VOUCHERS).json()["items"]

    assert {r["code"]: r["status"] for r in rows} == {
        "AKTIF": "active",
        "BITMIS": "expired",
        "BEKLEYEN": "scheduled",
        "TUKENMIS": "exhausted",
        "KAPALI": "inactive",
        "KAPALI-BITMIS": "inactive",
        "BITMIS-TUKENMIS": "expired",
    }


def test_the_list_can_be_narrowed_to_one_status(boss):
    past = {"starts_at": "2020-01-01T00:00:00Z", "ends_at": "2021-01-01T00:00:00Z"}
    for code, limits in (("AKTIF1", {}), ("AKTIF2", {}), ("BITMIS", past)):
        assert boss.post(VOUCHERS, {**ten_percent(code), **limits}).status_code == 201

    def codes(**query):
        response = boss.get(VOUCHERS, data=query)
        assert response.status_code == HTTPStatus.OK, response.content
        return [row["code"] for row in response.json()["items"]]

    assert codes(status="active") == ["AKTIF2", "AKTIF1"]
    assert codes(status="expired") == ["BITMIS"]
    assert codes(status="scheduled") == []
    assert codes() == ["BITMIS", "AKTIF2", "AKTIF1"]


def test_the_list_is_paged_and_the_total_counts_the_narrowed_list(boss):
    past = {"starts_at": "2020-01-01T00:00:00Z", "ends_at": "2021-01-01T00:00:00Z"}
    for code, limits in (("AAA1", {}), ("AAA2", {}), ("AAA3", {}), ("BBB1", past)):
        assert boss.post(VOUCHERS, {**ten_percent(code), **limits}).status_code == 201

    def page(**query):
        response = boss.get(VOUCHERS, data=query)
        assert response.status_code == HTTPStatus.OK, response.content
        body = response.json()
        return [r["code"] for r in body["items"]], body["total"], body["page"]

    assert page(page=1, page_size=3) == (["BBB1", "AAA3", "AAA2"], 4, 1)
    assert page(page=2, page_size=3) == (["AAA1"], 4, 2)
    assert page(status="active", page=2, page_size=2) == (["AAA1"], 3, 2)


def test_an_unknown_status_filter_is_refused_naming_the_field(boss):
    for bad in ("nope", "ACTIVE", ""):
        response = boss.get(VOUCHERS, data={"status": bad})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, bad
        assert list(response.json()["fields"]) == ["status"], bad


def kis50(boss):
    response = boss.post(
        VOUCHERS,
        {
            "code": "KIS50",
            "kind": "fixed",
            "value": "50",
            "minimum": "250",
            "starts_at": "2026-01-01T00:00:00Z",
            "ends_at": "2999-01-01T00:00:00Z",
            "cap": 100,
        },
    )
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()["id"]


def test_the_owner_edits_the_limits_and_every_edit_is_audited(boss, owner):
    voucher_id = kis50(boss)
    before = audit.AuditEntry.objects.get(action="voucher_created").after

    response = boss.patch(
        f"{VOUCHERS}/{voucher_id}",
        {
            "value": "60",
            "minimum": None,
            "starts_at": "2026-02-01T00:00:00Z",
            "ends_at": None,
            "cap": 200,
        },
    )

    assert response.status_code == HTTPStatus.OK, response.content
    edited = response.json()
    assert (edited["value"], edited["minimum"], edited["ends_at"]) == (
        "60.00",
        None,
        None,
    )
    assert (edited["cap"], edited["code"]) == (200, "KIS50")
    # Stored where the Storefront reads it: no minimum is 0, no expiry is the far date.
    voucher = Voucher.objects.get()
    offer = voucher.offers.get()
    assert offer.condition.value == 0
    assert offer.benefit.value == Decimal("60.00")
    assert offer.max_global_applications == 200
    assert voucher.end_datetime.year == 9999
    entry = audit.AuditEntry.objects.get(action="voucher_edited")
    assert (entry.actor, entry.target_id) == (owner, str(voucher.pk))
    assert "KIS50" in entry.summary
    assert entry.before == before
    assert entry.after == {
        **before,
        "value": "60.00",
        "minimum": None,
        "starts_at": "2026-02-01T00:00:00+00:00",
        "ends_at": None,
        "cap": 200,
    }

    # Leaving a field out keeps it as it is.
    only_value = boss.patch(f"{VOUCHERS}/{voucher_id}", {"value": "70"})
    assert only_value.status_code == HTTPStatus.OK, only_value.content
    assert (only_value.json()["cap"], only_value.json()["ends_at"]) == (200, None)
    assert only_value.json()["value"] == "70.00"


def test_the_code_and_the_once_per_customer_switch_lock_at_the_first_redemption(boss):
    voucher_id = kis50(boss)
    url = f"{VOUCHERS}/{voucher_id}"

    # Before any redemption both can change.
    free = boss.patch(url, {"code": " yeni50 ", "once_per_customer": True})
    assert free.status_code == HTTPStatus.OK, free.content
    assert (free.json()["code"], free.json()["once_per_customer"]) == ("YENI50", True)
    voucher = Voucher.objects.get()
    assert (voucher.code, voucher.usage) == ("YENI50", Voucher.ONCE_PER_CUSTOMER)

    redeem(voucher)

    # After it, a real change is refused naming the field, and nothing is changed.
    for changes, fields in (
        ({"code": "BASKA50"}, ["code"]),
        ({"once_per_customer": False}, ["once_per_customer"]),
        ({"code": "BASKA50", "once_per_customer": False, "cap": 7}, None),
    ):
        response = boss.patch(url, changes)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, changes
        if fields:
            assert list(response.json()["fields"]) == fields, changes
        else:
            assert set(response.json()["fields"]) == {"code", "once_per_customer"}
    voucher.refresh_from_db()
    assert (voucher.code, voucher.usage) == ("YENI50", Voucher.ONCE_PER_CUSTOMER)
    assert voucher.offers.get().max_global_applications == 100  # refused as a whole

    # Sending them back unchanged is fine, and the rest stays editable.
    kept = boss.patch(
        url,
        {
            "code": "yeni50",
            "once_per_customer": True,
            "value": "55",
            "minimum": "100",
            "ends_at": "2998-01-01T00:00:00Z",
            "cap": 150,
        },
    )
    assert kept.status_code == HTTPStatus.OK, kept.content
    assert (kept.json()["value"], kept.json()["cap"]) == ("55.00", 150)


def test_an_edit_keeps_every_limit_and_the_cap_stays_above_the_redemptions(boss):
    voucher_id = kis50(boss)
    url = f"{VOUCHERS}/{voucher_id}"
    voucher = Voucher.objects.get()
    for _ in range(3):
        redeem(voucher)

    refused = (
        ("cap", {"cap": 2}),  # three were already used
        ("cap", {"cap": 0}),
        ("minimum", {"minimum": "-0.01"}),
        ("value", {"value": "0"}),
        ("ends_at", {"ends_at": "2025-12-31T00:00:00Z"}),  # before the kept start
        ("ends_at", {"ends_at": "2026-01-01T00:00:00Z"}),  # the very moment it starts
        ("ends_at", {"starts_at": "2999-01-01T00:00:00Z"}),  # start meets the kept end
        ("ends_at", {"starts_at": "2999-06-01T00:00:00Z"}),
    )
    for field, changes in refused:
        response = boss.patch(url, changes)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, changes
        assert list(response.json()["fields"]) == [field], changes
    voucher.refresh_from_db()
    offer = voucher.offers.get()
    assert (offer.max_global_applications, offer.condition.value) == (100, 250)
    assert offer.benefit.value == 50
    assert not audit.AuditEntry.objects.filter(action="voucher_edited").exists()

    # The edges that are allowed: a cap equal to the redemptions, or no cap at all.
    assert boss.patch(url, {"cap": 3}).status_code == HTTPStatus.OK
    assert boss.patch(url, {"cap": None}).json()["cap"] is None
    assert boss.patch(url, {"minimum": "0"}).status_code == HTTPStatus.OK


def test_an_edit_cannot_take_another_vouchers_code_or_a_malformed_one(boss):
    voucher_id = kis50(boss)
    assert boss.post(VOUCHERS, ten_percent("YAZ10")).status_code == 201

    for bad in ("yaz10", " YAZ10 ", "AB", "YAZ 10", "İNDİRİM", "   "):
        response = boss.patch(f"{VOUCHERS}/{voucher_id}", {"code": bad})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, bad
        assert list(response.json()["fields"]) == ["code"], bad
    assert Voucher.objects.get(pk=voucher_id).code == "KIS50"


def test_editing_a_voucher_that_does_not_exist_is_a_404(boss):
    voucher_id = kis50(boss)

    response = boss.patch(f"{VOUCHERS}/{voucher_id + 1000}", {"value": "60"})

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert not audit.AuditEntry.objects.filter(action="voucher_edited").exists()
    assert Voucher.objects.get().offers.get().benefit.value == 50


def test_deactivating_stops_the_storefront_using_the_code_and_is_audited(
    boss,
    owner,
    shop,
):
    voucher_id = boss.post(VOUCHERS, ten_percent("YAZ10")).json()["id"]

    response = boss.post(f"{VOUCHERS}/{voucher_id}/deactivate")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "inactive"
    assert Voucher.objects.get().offers.get().status == ConditionalOffer.SUSPENDED
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    refused = guest.post(APPLY, {"code": "YAZ10"})
    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert refused.json()["fields"] == {"voucher_code": ["inactive"]}
    entry = audit.AuditEntry.objects.get(action="voucher_deactivated")
    assert (entry.actor, entry.target_id) == (owner, str(voucher_id))
    assert "YAZ10" in entry.summary
    assert (entry.before, entry.after) == ({"active": True}, {"active": False})


def test_activating_lets_the_storefront_use_the_code_again_and_is_audited(
    boss,
    owner,
    shop,
):
    voucher_id = boss.post(VOUCHERS, ten_percent("YAZ10")).json()["id"]
    boss.post(f"{VOUCHERS}/{voucher_id}/deactivate")

    response = boss.post(f"{VOUCHERS}/{voucher_id}/activate")

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "active"
    assert Voucher.objects.get().offers.get().status == ConditionalOffer.OPEN
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=2)
    applied = guest.post(APPLY, {"code": "YAZ10"})
    assert applied.status_code == HTTPStatus.OK, applied.content
    entry = audit.AuditEntry.objects.get(action="voucher_activated")
    assert (entry.actor, entry.target_id) == (owner, str(voucher_id))
    assert "YAZ10" in entry.summary
    assert (entry.before, entry.after) == ({"active": False}, {"active": True})


def test_activating_an_expired_voucher_does_not_bring_it_back_to_life(boss):
    past = {"starts_at": "2020-01-01T00:00:00Z", "ends_at": "2021-01-01T00:00:00Z"}
    voucher_id = boss.post(VOUCHERS, {**ten_percent("ESKI"), **past}).json()["id"]
    boss.post(f"{VOUCHERS}/{voucher_id}/deactivate")

    response = boss.post(f"{VOUCHERS}/{voucher_id}/activate")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "expired"


def test_repeating_deactivate_or_activate_changes_nothing_and_is_not_audited(boss):
    voucher_id = boss.post(VOUCHERS, ten_percent("YAZ10")).json()["id"]
    url = f"{VOUCHERS}/{voucher_id}"

    def entries(action):
        return audit.AuditEntry.objects.filter(action=action).count()

    assert boss.post(f"{url}/activate").json()["status"] == "active"  # already so
    assert entries("voucher_activated") == 0
    assert boss.post(f"{url}/deactivate").json()["status"] == "inactive"
    assert boss.post(f"{url}/deactivate").json()["status"] == "inactive"
    assert entries("voucher_deactivated") == 1
    assert boss.post(f"{url}/activate").json()["status"] == "active"
    assert boss.post(f"{url}/activate").json()["status"] == "active"
    assert entries("voucher_activated") == 1


def test_a_voucher_that_does_not_exist_cannot_be_activated_or_deactivated(boss):
    for action in ("activate", "deactivate"):
        response = boss.post(f"{VOUCHERS}/4242/{action}")
        assert response.status_code == HTTPStatus.NOT_FOUND, action
    assert not audit.AuditEntry.objects.filter(action__startswith="voucher_").exists()


def test_deleting_an_unused_voucher_removes_it_and_is_audited(boss, owner, shop):
    voucher_id = kis50(boss)
    before = audit.AuditEntry.objects.get(action="voucher_created").after
    # It sits in a Guest's basket: that alone does not block the delete.
    guest = Guest()
    fill(guest, shop[1]["M"], quantity=4)
    assert guest.post(APPLY, {"code": "KIS50"}).status_code == HTTPStatus.OK

    response = boss.delete(f"{VOUCHERS}/{voucher_id}")

    assert response.status_code == HTTPStatus.NO_CONTENT, response.content
    assert Voucher.objects.count() == 0
    assert ConditionalOffer.objects.count() == 0
    assert Benefit.objects.count() == 0
    assert Condition.objects.count() == 0
    assert Range.objects.filter(name="Tüm ürünler").count() == 1  # shared: it stays
    entry = audit.AuditEntry.objects.get(action="voucher_deleted")
    assert (entry.actor, entry.target_id) == (owner, str(voucher_id))
    assert "KIS50" in entry.summary
    assert (entry.before, entry.after) == (before, None)
    # The Guest's basket still works, without the voucher.
    basket = guest.get("/api/store/basket")
    assert basket.status_code == HTTPStatus.OK, basket.content
    assert basket.json()["voucher"] is None
    assert boss.get(VOUCHERS).json()["items"] == []


def test_a_redeemed_voucher_cannot_be_deleted_and_the_refusal_names_the_count(boss):
    voucher_id = kis50(boss)
    voucher = Voucher.objects.get()
    for _ in range(2):
        redeem(voucher)

    response = boss.delete(f"{VOUCHERS}/{voucher_id}")

    assert response.status_code == HTTPStatus.CONFLICT, response.content
    assert response.json()["code"] == "voucher_redeemed"
    assert "2 kez" in response.json()["message"]
    assert Voucher.objects.filter(pk=voucher_id).exists()
    assert voucher.offers.count() == 1
    assert not audit.AuditEntry.objects.filter(action="voucher_deleted").exists()
    # Deactivating is what the Owner can still do.
    assert boss.post(f"{VOUCHERS}/{voucher_id}/deactivate").status_code == HTTPStatus.OK


def test_deleting_a_voucher_that_does_not_exist_is_a_404(boss, staff):
    voucher_id = kis50(boss)

    assert boss.delete(f"{VOUCHERS}/{voucher_id + 1000}").status_code == 404
    assert Voucher.objects.count() == 1
    # And Staff cannot delete at all.
    assert staff.delete(f"{VOUCHERS}/{voucher_id}").status_code == HTTPStatus.FORBIDDEN
    assert Voucher.objects.count() == 1
    assert not audit.AuditEntry.objects.filter(action="voucher_deleted").exists()


def test_the_admin_order_shows_the_voucher_and_keeps_its_lines_as_charged(
    boss,
    staff,
    shop,
):
    assert boss.post(VOUCHERS, ten_percent("YAZ10")).status_code == HTTPStatus.CREATED
    order = place(open_basket(shop[1]["M"]), "yaz10")

    for who in (boss, staff):
        response = who.get(f"/api/admin/orders/{order.number}")

        assert response.status_code == HTTPStatus.OK, response.content
        detail = response.json()
        assert detail["voucher"] == {"code": "YAZ10", "discount": "20.00"}
        # Lines stay as charged (after the voucher), so the figures still add up.
        assert [line["line_total"] for line in detail["lines"]] == ["180.00"]
        assert detail["items_total"] == "180.00"
        parts = (detail["items_total"], detail["shipping_fee"], detail["cod_fee"])
        assert sum(Decimal(part) for part in parts) == Decimal(detail["total"])


def test_an_order_without_a_voucher_shows_none(boss, shop):
    # A voucher exists, but this order does not use it.
    assert boss.post(VOUCHERS, ten_percent("YAZ10")).status_code == HTTPStatus.CREATED
    order = place(open_basket(shop[1]["M"]))

    response = boss.get(f"/api/admin/orders/{order.number}")

    assert response.status_code == HTTPStatus.OK, response.content
    detail = response.json()
    assert detail["voucher"] is None
    assert [line["line_total"] for line in detail["lines"]] == ["200.00"]
    assert detail["items_total"] == "200.00"


def test_later_edits_to_the_voucher_never_change_what_the_order_shows(boss, shop):
    voucher_id = boss.post(VOUCHERS, ten_percent("YAZ10")).json()["id"]
    order = place(open_basket(shop[1]["M"]), "YAZ10")
    url = f"/api/admin/orders/{order.number}"
    shown = boss.get(url).json()
    assert shown["voucher"] == {"code": "YAZ10", "discount": "20.00"}

    # Value, dates and cap stay editable after a redemption, and it can be closed.
    edit = boss.patch(f"{VOUCHERS}/{voucher_id}", {"value": "50", "cap": 10})
    assert edit.status_code == HTTPStatus.OK, edit.content
    assert boss.post(f"{VOUCHERS}/{voucher_id}/deactivate").status_code == 200

    after = boss.get(url).json()
    assert after["voucher"] == {"code": "YAZ10", "discount": "20.00"}
    assert after["lines"] == shown["lines"]
    assert (after["items_total"], after["total"]) == (
        shown["items_total"],
        shown["total"],
    )
