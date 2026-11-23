"""The Owner changes the URL slug of a Legal page (phase 4, section 5)."""

from http import HTTPStatus

import pytest

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.services import legal_pages
from store_backend.shop.models import LEGAL_PAGE_META
from store_backend.shop.models import LEGAL_PAGES
from store_backend.shop.models import LegalPageVersion
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

PUBLIC = "/api/store/legal-pages"
ADMIN = "/api/admin/legal-pages"
RETURNS = "returns_withdrawal"
RETURNS_SLUG = "iade-ve-cayma-kosullari"
PRIVACY = "privacy_notice"
PRIVACY_SLUG = "gizlilik-ve-kvkk"


def refused(slug, page=RETURNS, who=None):
    with pytest.raises(UnprocessableError) as caught:
        legal_pages.change_slug(who, page, slug)
    return caught.value


# --- the service ------------------------------------------------------------------


def test_every_page_starts_with_its_original_slug_and_no_redirects():
    for page, (slug, _title) in LEGAL_PAGE_META.items():
        assert legal_pages.slug_of(page) == slug
        assert legal_pages.redirects(page) == []


def test_changing_the_slug_moves_the_page_and_keeps_the_old_address(owner):
    legal_pages.change_slug(owner, RETURNS, "iade-kosullari")

    assert legal_pages.slug_of(RETURNS) == "iade-kosullari"
    assert legal_pages.redirects(RETURNS) == [RETURNS_SLUG]
    assert legal_pages.page_for_slug("iade-kosullari") == RETURNS
    assert legal_pages.page_for_slug(RETURNS_SLUG) == RETURNS


def test_changing_twice_keeps_every_earlier_address_redirecting(owner):
    legal_pages.change_slug(owner, RETURNS, "iade-kosullari")
    legal_pages.change_slug(owner, RETURNS, "iade")

    assert legal_pages.slug_of(RETURNS) == "iade"
    assert legal_pages.redirects(RETURNS) == [RETURNS_SLUG, "iade-kosullari"]
    assert legal_pages.page_for_slug(RETURNS_SLUG) == RETURNS


def test_going_back_to_an_earlier_slug_makes_the_other_one_a_redirect(owner):
    legal_pages.change_slug(owner, RETURNS, "iade")
    legal_pages.change_slug(owner, RETURNS, RETURNS_SLUG)

    assert legal_pages.slug_of(RETURNS) == RETURNS_SLUG
    assert legal_pages.redirects(RETURNS) == ["iade"]


def test_the_page_key_the_versions_and_consent_point_at_do_not_change(owner):
    before = list(LegalPageVersion.objects.values_list("page", "version"))
    versions_before = legal_pages.current_versions()

    legal_pages.change_slug(owner, RETURNS, "iade")

    assert list(LegalPageVersion.objects.values_list("page", "version")) == before
    assert legal_pages.current_versions() == versions_before
    assert legal_pages.current(RETURNS).page == RETURNS


def test_the_change_is_audited_with_the_old_and_new_slug(owner):
    legal_pages.change_slug(owner, RETURNS, "iade")

    entry = AuditEntry.objects.get(action="legal_slug_changed")
    assert entry.actor == owner
    assert entry.target_type == "legal_page"
    assert entry.target_id == RETURNS
    assert entry.before == {"slug": RETURNS_SLUG}
    assert entry.after == {"slug": "iade"}


def test_the_slug_is_trimmed(owner):
    legal_pages.change_slug(owner, RETURNS, "  iade  ")
    assert legal_pages.slug_of(RETURNS) == "iade"


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "   ",
        "Iade",
        "iade kosullari",
        "iade_kosullari",
        "iade--kosullari",
        "-iade",
        "iade-",
        "iadé",
        "a" * 101,
    ],
)
def test_a_slug_must_be_lowercase_letters_digits_and_single_hyphens(owner, slug):
    error = refused(slug, who=owner)
    assert "slug" in error.fields
    assert legal_pages.slug_of(RETURNS) == RETURNS_SLUG


def test_a_slug_the_router_already_uses_is_refused(owner):
    assert "slug" in refused("search", who=owner).fields


def test_the_same_slug_is_refused_as_no_change(owner):
    assert "slug" in refused(RETURNS_SLUG, who=owner).fields
    assert AuditEntry.objects.filter(action="legal_slug_changed").count() == 0


def test_another_pages_current_slug_is_refused(owner):
    assert "slug" in refused(PRIVACY_SLUG, who=owner).fields


def test_another_pages_old_slug_is_refused_so_its_links_stay_valid(owner):
    legal_pages.change_slug(owner, PRIVACY, "gizlilik")
    assert "slug" in refused(PRIVACY_SLUG, who=owner).fields
    assert legal_pages.page_for_slug(PRIVACY_SLUG) == PRIVACY


def test_an_unknown_page_is_not_found(owner):
    with pytest.raises(NotFoundError):
        legal_pages.change_slug(owner, "nope", "iade")


def test_an_unknown_slug_is_not_found():
    with pytest.raises(NotFoundError):
        legal_pages.page_for_slug("nope")


# --- the public API follows the change --------------------------------------------


def test_the_public_page_is_read_by_its_old_or_new_slug_and_says_its_current_one(
    client,
    owner,
):
    legal_pages.change_slug(owner, RETURNS, "iade")

    for slug in ("iade", RETURNS_SLUG):
        body = client.get(f"{PUBLIC}/{slug}").json()
        assert body["slug"] == "iade"
        assert body["page"] == RETURNS


def test_an_older_version_is_read_through_the_old_slug_too(client, owner):
    legal_pages.change_slug(owner, RETURNS, "iade")
    response = client.get(f"{PUBLIC}/{RETURNS_SLUG}/versions/1")
    assert response.status_code == HTTPStatus.OK
    assert response.json()["slug"] == "iade"


def test_the_public_list_uses_the_new_slug(client, owner):
    legal_pages.change_slug(owner, RETURNS, "iade")
    slugs = {row["page"]: row["slug"] for row in client.get(PUBLIC).json()}
    assert slugs[RETURNS] == "iade"
    assert slugs[PRIVACY] == PRIVACY_SLUG


def test_search_hits_carry_the_new_slug(client, owner):
    legal_pages.change_slug(owner, RETURNS, "iade")
    hits = client.get(f"{PUBLIC}/search", {"q": "iade"}).json()
    assert {h["slug"] for h in hits if h["page"] == RETURNS} <= {"iade"}


def test_menu_items_follow_the_new_slug_without_an_edit(client, owner):
    from store_backend.services import menus  # noqa: PLC0415

    legal_pages.change_slug(owner, RETURNS, "iade")
    urls = {
        node["url"]
        for items in menus.public().values()
        for node in items
        if node.get("url")
    }
    assert "/yasal/iade" in urls
    assert f"/yasal/{RETURNS_SLUG}" not in urls


# --- the Dashboard API ------------------------------------------------------------


def test_the_owner_changes_a_slug_over_the_api_and_sees_the_redirects(owner, api_for):
    boss = api_for(owner, enrol=True)

    done = boss.put(f"{ADMIN}/{RETURNS}/slug", {"slug": "iade"})

    assert done.status_code == HTTPStatus.OK
    assert done.json()["slug"] == "iade"
    assert done.json()["redirects"] == [RETURNS_SLUG]
    rows = {row["page"]: row for row in boss.get(ADMIN).json()}
    assert rows[RETURNS]["slug"] == "iade"
    assert rows[RETURNS]["redirects"] == [RETURNS_SLUG]
    assert rows[PRIVACY]["redirects"] == []


def test_the_versions_list_carries_the_redirects_the_editor_shows(owner, api_for):
    boss = api_for(owner, enrol=True)
    boss.put(f"{ADMIN}/{RETURNS}/slug", {"slug": "iade"})

    versions = boss.get(f"{ADMIN}/{RETURNS}/versions").json()

    assert versions[0]["slug"] == "iade"
    assert versions[0]["redirects"] == [RETURNS_SLUG]


def test_a_bad_slug_over_the_api_is_422_with_the_field(owner, api_for):
    boss = api_for(owner, enrol=True)
    response = boss.put(f"{ADMIN}/{RETURNS}/slug", {"slug": "Iade Kosullari"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "slug" in str(response.json())


def test_an_unknown_page_over_the_api_is_404(owner, api_for):
    boss = api_for(owner, enrol=True)
    assert boss.put(f"{ADMIN}/nope/slug", {"slug": "iade"}).status_code == (
        HTTPStatus.NOT_FOUND
    )


def test_staff_cannot_change_a_slug(staff_user, api_for):
    member = api_for(staff_user)
    response = member.put(f"{ADMIN}/{RETURNS}/slug", {"slug": "iade"})
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert legal_pages.slug_of(RETURNS) == RETURNS_SLUG


def test_every_page_can_be_renamed_and_the_slugs_stay_unique(owner):
    for page in LEGAL_PAGES:
        legal_pages.change_slug(owner, page, f"yeni-{page.replace('_', '-')}")
    slugs = [legal_pages.slug_of(p) for p in LEGAL_PAGES]
    assert len(set(slugs)) == len(LEGAL_PAGES)
