# ruff: noqa: PLR2004, F811
"""The Owner edits the Homepage layout: read, save the whole list, audited (#156)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client

from store_backend.catalog.models import HomepageLayout
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import audit
from store_backend.services import homepage
from store_backend.staff.models import AuditEntry
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


def test_staff_cannot_read_or_save_the_layout(staff):
    assert staff.get(ADMIN).status_code == HTTPStatus.FORBIDDEN
    assert staff.put(ADMIN, {"sections": []}).status_code == HTTPStatus.FORBIDDEN


def test_a_visitor_cannot_read_or_save_the_layout():
    client = Client()

    assert client.get(ADMIN).status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    )
    assert client.put(
        ADMIN,
        {"sections": []},
        content_type="application/json",
    ).status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


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


def test_a_shopper_cannot_request_the_preview():
    assert Client().get(PREVIEW).status_code in {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }


def test_the_storefront_endpoint_ignores_a_preview_request():
    response = Client().get(STORE, {"preview": "1"})

    assert response.status_code == HTTPStatus.OK
    assert response.json() == Client().get(STORE).json()
