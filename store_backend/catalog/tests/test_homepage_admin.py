# ruff: noqa: PLR2004, F811
"""The Owner edits the Homepage layout: read, save the whole list, audited (#156)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.api.errors import ForbiddenError
from store_backend.catalog.models import HomepageLayout
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import approval
from store_backend.services import audit
from store_backend.services import homepage
from store_backend.staff.models import ApprovedVersion
from store_backend.staff.models import AuditEntry
from store_backend.staff.models import PendingChange
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin/homepage"
STORE = "/api/store/homepage"


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


def read(boss) -> list[dict]:
    response = boss.get(ADMIN)
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()["sections"]


def save(boss, sections: list[dict]):
    return boss.put(ADMIN, {"sections": sections})


def test_the_owner_reads_every_section_of_the_layout(boss):
    sections = read(boss)

    assert [s["id"] for s in sections] == [s["id"] for s in homepage.default_sections()]
    latest = next(s for s in sections if s["id"] == "latest")
    assert latest["settings"] == {
        "source": "latest",
        "count": 12,
        "window": 30,
        "category_id": None,
        "product_ids": [],
    }
    assert latest["visible"] is True
    assert latest["display"] == "carousel"


def test_hidden_sections_are_still_listed_for_editing(boss):
    sections = read(boss)
    next(s for s in sections if s["id"] == "latest")["visible"] = False
    assert save(boss, sections).status_code == HTTPStatus.OK

    assert next(s for s in read(boss) if s["id"] == "latest")["visible"] is False


def test_saving_replaces_the_whole_layout_in_the_order_sent(boss):
    sections = read(boss)
    sections.reverse()

    response = save(boss, sections)

    assert response.status_code == HTTPStatus.OK, response.content
    assert [s["id"] for s in response.json()["sections"]] == [s["id"] for s in sections]
    stored = HomepageLayout.objects.get().sections
    assert [s["id"] for s in stored] == [s["id"] for s in sections]


def test_a_hidden_section_keeps_its_settings(boss):
    sections = read(boss)
    latest = next(s for s in sections if s["id"] == "latest")
    latest["settings"]["count"] = 6
    latest["visible"] = False
    save(boss, sections)

    saved = next(s for s in read(boss) if s["id"] == "latest")

    assert saved["visible"] is False
    assert saved["settings"]["count"] == 6


def test_the_common_settings_can_be_changed(boss):
    sections = read(boss)
    latest = next(s for s in sections if s["id"] == "latest")
    latest.update(title="Yeni sezon", link="/ara?sort=newest", display="grid")
    save(boss, sections)

    saved = next(s for s in read(boss) if s["id"] == "latest")

    assert (saved["title"], saved["link"], saved["display"]) == (
        "Yeni sezon",
        "/ara?sort=newest",
        "grid",
    )


def test_a_new_section_can_be_added_and_one_removed(boss):
    sections = [s for s in read(boss) if s["id"] != "categories"]
    sections.append(
        {
            "id": "duyuru",
            "type": "rich_text",
            "title": "Duyuru",
            "settings": {"body": "Merhaba"},
        },
    )

    assert save(boss, sections).status_code == HTTPStatus.OK

    ids = [s["id"] for s in read(boss)]
    assert "categories" not in ids
    assert ids[-1] == "duyuru"


def test_the_storefront_follows_the_saved_layout(boss):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 3)])
    sections = read(boss)
    next(s for s in sections if s["id"] == "latest")["visible"] = False
    sections.reverse()
    save(boss, sections)

    shown = [s["id"] for s in Client().get(STORE).json()["sections"]]

    assert "latest" not in shown
    assert shown == ["best-selling", "recently-viewed", "hero"]


def test_every_save_is_written_to_the_audit_log(boss, owner):
    before = read(boss)
    changed = [dict(s) for s in before]
    changed[0]["visible"] = False

    save(boss, changed)

    entry = AuditEntry.objects.get(action=audit.Action.HOMEPAGE_SAVED)
    assert entry.actor_id == owner.pk
    assert entry.before["sections"][0]["visible"] is True
    assert entry.after["sections"][0]["visible"] is False


def test_a_second_save_is_audited_again(boss):
    sections = read(boss)
    save(boss, sections)
    save(boss, sections)

    assert AuditEntry.objects.filter(action=audit.Action.HOMEPAGE_SAVED).count() == 2


def test_an_unknown_section_type_is_refused(boss):
    response = save(boss, [{"id": "x", "type": "carousel3d"}])

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_unknown_settings_are_refused(boss):
    response = save(
        boss,
        [{"id": "x", "type": "products", "settings": {"colour": "red"}}],
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_duplicate_ids_are_refused_and_nothing_is_saved(boss):
    before = read(boss)

    response = save(
        boss,
        [{"id": "x", "type": "slider"}, {"id": "x", "type": "slider"}],
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert read(boss) == before
    assert not AuditEntry.objects.filter(action=audit.Action.HOMEPAGE_SAVED).exists()


def test_more_than_twenty_sections_are_refused(boss):
    response = save(boss, [{"id": f"s{n}", "type": "slider"} for n in range(21)])

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# --- the Approval rule (#160) -------------------------------------------------------


def hidden_first(sections: list[dict]) -> list[dict]:
    changed = [dict(s) for s in sections]
    changed[0]["visible"] = False
    return changed


def detail(api) -> dict:
    return api.get(ADMIN).json()


def test_a_staff_save_waits_and_the_live_layout_is_unchanged(boss, staff):
    live = read(boss)

    response = save(staff, hidden_first(live))

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["pending"]["sections"][0]["visible"] is False
    assert response.json()["sections"][0]["visible"] is True
    assert read(boss) == live
    assert HomepageLayout.objects.get().sections[0].get("visible", True) is True


def test_a_second_staff_save_amends_the_one_pending_change(boss, staff):
    live = read(boss)
    save(staff, hidden_first(live))
    again = [dict(s) for s in live]
    again[1]["title"] = "Başka"

    save(staff, again)

    assert PendingChange.objects.count() == 1
    pending = detail(boss)["pending"]["sections"]
    assert pending[0]["visible"] is True
    assert pending[1]["title"] == "Başka"


def test_a_staff_save_is_checked_like_the_owners(staff):
    response = save(staff, [{"id": "x", "type": "carousel3d"}])

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert not PendingChange.objects.exists()


def test_the_pending_layout_appears_in_the_queue_with_its_count(boss, staff):
    save(staff, hidden_first(read(boss)))

    queue = boss.get("/api/admin/approvals").json()

    assert queue["count"] == 1
    assert (queue["items"][0]["type"], queue["items"][0]["is_change"]) == (
        "homepage",
        True,
    )
    assert queue["items"][0]["label"] == "Vitrin"


def test_the_owner_approves_and_the_layout_goes_live(boss, staff):
    save(staff, hidden_first(read(boss)))

    response = boss.post("/api/admin/approvals/homepage/1/approve")

    assert response.status_code == HTTPStatus.OK, response.content
    assert read(boss)[0]["visible"] is False
    assert detail(boss)["pending"] is None
    assert ApprovedVersion.objects.count() == 1
    assert AuditEntry.objects.filter(
        action=audit.Action.APPROVAL_CHANGE_APPROVED,
    ).exists()


def test_the_owner_rejects_with_a_reason_the_staff_member_can_read(boss, staff):
    live = read(boss)
    save(staff, hidden_first(live))

    response = boss.post(
        "/api/admin/approvals/homepage/1/reject",
        {"reason": "Sıra yanlış"},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert read(boss) == live
    after = detail(staff)
    assert after["pending"] is None
    assert after["rejection_reason"] == "Sıra yanlış"
    assert AuditEntry.objects.filter(
        action=audit.Action.APPROVAL_CHANGE_REJECTED,
    ).exists()


def test_staff_cannot_approve_or_reject_their_own_change(boss, staff):
    live = read(boss)
    save(staff, hidden_first(live))

    approve = staff.post("/api/admin/approvals/homepage/1/approve")
    reject = staff.post("/api/admin/approvals/homepage/1/reject", {"reason": "x"})

    assert approve.status_code == HTTPStatus.FORBIDDEN
    assert reject.status_code == HTTPStatus.FORBIDDEN
    assert read(boss) == live
    assert PendingChange.objects.count() == 1


def test_staff_cannot_approve_through_the_service_either(staff_user, api_for, boss):
    save(api_for(staff_user), hidden_first(read(boss)))

    with pytest.raises(ForbiddenError):
        approval.approve(staff_user, homepage.KIND, 1)


def test_the_owner_cannot_save_while_a_change_is_waiting(boss, staff):
    live = read(boss)
    save(staff, hidden_first(live))

    response = save(boss, live)

    assert response.status_code == HTTPStatus.CONFLICT
    assert detail(boss)["pending"] is not None


def test_a_staff_member_sees_the_live_layout_and_their_pending_change(boss, staff):
    save(staff, hidden_first(read(boss)))

    seen = detail(staff)

    assert seen["sections"][0]["visible"] is True
    assert seen["pending"]["sections"][0]["visible"] is False


def test_every_step_is_in_the_audit_log(boss, staff):
    save(staff, hidden_first(read(boss)))
    boss.post("/api/admin/approvals/homepage/1/approve")

    actions = set(AuditEntry.objects.values_list("action", flat=True))

    assert {
        audit.Action.APPROVAL_CHANGE_SUBMITTED,
        audit.Action.APPROVAL_CHANGE_APPROVED,
    } <= actions


def test_an_unauthenticated_request_is_refused():
    assert Client().get(ADMIN).status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    )


PREVIEW = "/api/admin/homepage/preview"


def test_the_owner_previews_the_layout_as_the_storefront_draws_it(boss):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 3)])
    sections = read(boss)
    next(s for s in sections if s["id"] == "latest")["visible"] = False
    save(boss, sections)

    response = boss.get(PREVIEW)

    assert response.status_code == HTTPStatus.OK, response.content
    shown = [s["id"] for s in response.json()["sections"]]
    assert shown == [s["id"] for s in Client().get(STORE).json()["sections"]]
    assert "latest" not in shown


def test_the_preview_shows_the_pending_layout_to_staff_while_the_storefront_does_not(
    boss,
    staff,
):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 3)])
    sections = read(boss)
    next(s for s in sections if s["id"] == "latest")["visible"] = False
    save(staff, sections)

    assert "latest" in [s["id"] for s in Client().get(STORE).json()["sections"]]
    for who in (staff, boss):
        response = who.get(PREVIEW)
        assert response.status_code == HTTPStatus.OK, response.content
        assert "latest" not in [s["id"] for s in response.json()["sections"]]


def test_a_shopper_cannot_request_the_preview():
    assert Client().get(PREVIEW).status_code in {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }


def test_the_storefront_endpoint_ignores_a_preview_request():
    response = Client().get(STORE, {"preview": "1"})

    assert response.status_code == HTTPStatus.OK
    assert response.json() == Client().get(STORE).json()
