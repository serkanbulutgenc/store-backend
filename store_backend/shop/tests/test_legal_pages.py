# ruff: noqa: PLR2004
from http import HTTPStatus

import pytest

from store_backend.api.errors import NotFoundError
from store_backend.services import legal_pages
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import ImmutableError
from store_backend.shop.models import LegalPageVersion
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

PUBLIC = "/api/store/legal-pages"
ADMIN = "/api/admin/legal-pages"
RETURNS = "returns_withdrawal"
RETURNS_SLUG = "iade-ve-cayma-kosullari"


def publish(owner, page=RETURNS, *, title="Yeni başlık", body="Yeni metin"):
    return legal_pages.publish(owner, page, title=title, body=body)


# --- the carried-over text --------------------------------------------------------


def test_every_page_starts_with_its_carried_over_text_at_version_one():
    assert legal_pages.current_versions() == dict.fromkeys(LEGAL_PAGES, "1")
    assert all(v.body.strip() for v in legal_pages.current_all())


# --- publishing -------------------------------------------------------------------


def test_publishing_adds_the_next_version_and_keeps_the_old_one(owner):
    first = legal_pages.current(RETURNS)

    second = publish(owner)

    assert second.version == first.version + 1
    assert legal_pages.current(RETURNS).pk == second.pk
    assert legal_pages.get_version(RETURNS, first.version).body == first.body
    assert [v.version for v in legal_pages.versions(RETURNS)] == [2, 1]
    assert legal_pages.current_versions()[RETURNS] == "2"
    assert legal_pages.current_versions()["privacy_notice"] == "1"


def test_publishing_is_audited_without_the_text(owner):
    publish(owner)

    entry = AuditEntry.objects.get(action="legal_page_published")
    assert entry.actor == owner
    assert entry.before == {"version": 1}
    assert entry.after == {"version": 2, "title": "Yeni başlık"}


def test_an_unchanged_text_does_not_take_a_new_version(owner):
    current = legal_pages.current(RETURNS)

    with pytest.raises(legal_pages.UnprocessableError):
        publish(owner, title=current.title, body=current.body)

    assert legal_pages.current_versions()[RETURNS] == "1"


@pytest.mark.parametrize(
    ("title", "body", "field"),
    [
        (" ", "metin", "title"),
        ("a" * 201, "metin", "title"),
        ("başlık", "  ", "body"),
        ("başlık", "a" * 200_001, "body"),
    ],
)
def test_invalid_text_is_refused(owner, title, body, field):
    with pytest.raises(legal_pages.UnprocessableError) as caught:
        publish(owner, title=title, body=body)

    assert field in caught.value.fields


def test_an_unknown_page_is_not_found(owner):
    with pytest.raises(NotFoundError):
        publish(owner, "made_up")


# --- immutability -----------------------------------------------------------------


def test_a_saved_version_cannot_be_changed_or_removed():
    version = legal_pages.current(RETURNS)
    version.body = "başka"

    with pytest.raises(ImmutableError):
        version.save()
    with pytest.raises(ImmutableError):
        version.delete()
    with pytest.raises(ImmutableError):
        LegalPageVersion.objects.update(body="x")
    with pytest.raises(ImmutableError):
        LegalPageVersion.objects.all().delete()


# --- the public API ---------------------------------------------------------------


def test_the_public_list_shows_the_current_version_of_every_page(client, owner):
    publish(owner)

    body = client.get(PUBLIC).json()

    assert [row["page"] for row in body] == list(LEGAL_PAGES)
    returns = next(row for row in body if row["page"] == RETURNS)
    assert returns["version"] == 2
    assert returns["slug"] == RETURNS_SLUG


def test_a_page_is_read_by_slug_and_an_older_version_stays_readable(client, owner):
    old = legal_pages.current(RETURNS)
    publish(owner, body="Yeni metin")

    current = client.get(f"{PUBLIC}/{RETURNS_SLUG}").json()
    older = client.get(f"{PUBLIC}/{RETURNS_SLUG}/versions/1").json()

    assert current["version"] == 2
    assert current["body"] == "Yeni metin"
    assert older["version"] == 1
    assert older["body"] == old.body


def test_an_unknown_slug_or_version_is_404(client):
    assert client.get(f"{PUBLIC}/nope").status_code == HTTPStatus.NOT_FOUND
    assert (
        client.get(f"{PUBLIC}/{RETURNS_SLUG}/versions/9").status_code
        == HTTPStatus.NOT_FOUND
    )


# --- search -----------------------------------------------------------------------


def test_search_finds_a_word_in_the_body(client):
    hits = client.get(f"{PUBLIC}/search", {"q": "cayma"}).json()

    assert [h["page"] for h in hits] == [RETURNS]
    assert hits[0]["slug"] == RETURNS_SLUG
    assert "«" in hits[0]["excerpt"]


def test_search_looks_only_at_the_current_version(client, owner):
    publish(owner, title="İade", body="Yeni metin başka bir şey anlatır.")

    assert client.get(f"{PUBLIC}/search", {"q": "cayma"}).json() == []
    hits = client.get(f"{PUBLIC}/search", {"q": "anlatır"}).json()
    assert [h["page"] for h in hits] == [RETURNS]


def test_a_short_or_empty_search_returns_nothing(client):
    assert client.get(f"{PUBLIC}/search", {"q": "a"}).json() == []
    assert client.get(f"{PUBLIC}/search").json() == []


# --- the Dashboard API ------------------------------------------------------------


def test_the_owner_publishes_through_the_api(owner, api_for):
    api = api_for(owner, enrol=True)

    response = api.post(
        f"{ADMIN}/{RETURNS}/versions",
        {"title": "İade koşulları", "body": "Metin"},
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["version"] == 2
    assert response.json()["created_by"] == owner.email
    listing = api.get(ADMIN).json()
    assert next(r for r in listing if r["page"] == RETURNS)["version"] == 2
    versions = api.get(f"{ADMIN}/{RETURNS}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert api.get(f"{ADMIN}/{RETURNS}/versions/2").json()["body"] == "Metin"


def test_publishing_an_unchanged_text_over_the_api_is_422(owner, api_for):
    current = legal_pages.current(RETURNS)

    response = api_for(owner, enrol=True).post(
        f"{ADMIN}/{RETURNS}/versions",
        {"title": current.title, "body": current.body},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_staff_cannot_read_or_publish(staff_user, api_for):
    api = api_for(staff_user)

    assert api.get(ADMIN).status_code == HTTPStatus.FORBIDDEN
    assert (
        api.post(f"{ADMIN}/{RETURNS}/versions", {"title": "t", "body": "b"}).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert legal_pages.current_versions()[RETURNS] == "1"
