# ruff: noqa: PLR2004
"""A Collection's cover image: upload, replace, remove, who may, and the Storefront."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import Collection
from store_backend.catalog.models import PendingFileRemoval
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.services import catalog
from store_backend.services import collection_admin
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

COLLECTIONS = "/api/admin/collections"
STORE = "/api/store/collections"


@pytest.fixture
def boss(owner, api_for):  # noqa: F811
    return api_for(owner, enrol=True)


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


def make_product(title="Elbise"):
    made = catalog.create_product(
        title=title,
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
    )
    catalog.create_variant(
        made,
        colour=catalog.create_colour(f"Renk {title}", "#112233"),
        size="M",
        stock=5,
    )
    ProductProfile.objects.filter(product=made).update(status=ProductStatus.PUBLISHED)
    return made


def create(client, **fields):
    response = client.post(COLLECTIONS, {"name": "Editörün seçimi", **fields})
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def published(boss):
    made = create(boss, product_ids=[make_product().pk])
    assert boss.post(f"{COLLECTIONS}/{made['id']}/publish").status_code == 200
    return made


def image_url(made):
    return f"{COLLECTIONS}/{made['id']}/image"


def upload(client, made, **kwargs):
    return post_file(client, image_url(made), make_file(**kwargs))


def store_page(slug="editorun-secimi"):
    return Client().get(f"{STORE}/{slug}")


# --- the Owner ------------------------------------------------------------------------


def test_a_new_collection_has_no_image(boss):
    assert create(boss)["image"] is None


def test_the_owner_uploads_an_image_that_is_processing_until_derivatives_exist(boss):
    made = create(boss)

    response = upload(boss, made, size=(1800, 1200))

    assert response.status_code == HTTPStatus.OK, response.content
    image = response.json()["image"]
    assert image["status"] == "processing"
    assert image["derivatives"] == []
    assert (image["width"], image["height"]) == (1800, 1200)

    collection_admin.process_image(made["id"])

    ready = boss.get(f"{COLLECTIONS}/{made['id']}").json()["image"]
    assert ready["status"] == "ready"
    assert [d["width"] for d in ready["derivatives"]] == [400, 800, 1600]


def test_the_alt_text_is_set_on_upload_and_changed_after(boss):
    made = create(boss)
    response = post_file(
        boss,
        image_url(made),
        make_file(),
        alt_text="Sonbahar kombini",
    )
    assert response.json()["image"]["alt_text"] == "Sonbahar kombini"

    changed = boss.patch(image_url(made), {"alt_text": "Yeni açıklama"})

    assert changed.status_code == HTTPStatus.OK
    assert changed.json()["image"]["alt_text"] == "Yeni açıklama"


def test_alt_text_over_the_limit_is_refused(boss):
    made = create(boss)
    upload(boss, made)

    response = boss.patch(image_url(made), {"alt_text": "a" * 256})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_changing_the_alt_of_a_collection_without_an_image_is_refused(boss):
    made = create(boss)

    response = boss.patch(image_url(made), {"alt_text": "x"})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "collection_no_image"


def test_a_bad_image_is_refused_and_leaves_the_collection_alone(boss):
    made = create(boss)

    response = upload(boss, made, size=(300, 200))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert boss.get(f"{COLLECTIONS}/{made['id']}").json()["image"] is None


def test_replacing_and_removing_schedule_the_old_files_for_removal(boss):
    made = create(boss)
    upload(boss, made, colour=(1, 2, 3))
    collection_admin.process_image(made["id"])
    first = Collection.objects.get(pk=made["id"]).image_file_names()
    assert first

    upload(boss, made, colour=(9, 9, 9))
    queued = set(PendingFileRemoval.objects.values_list("name", flat=True))
    assert set(first) <= queued

    collection_admin.process_image(made["id"])
    second = Collection.objects.get(pk=made["id"]).image_file_names()
    removed = boss.delete(image_url(made))

    assert removed.status_code == HTTPStatus.OK
    assert removed.json()["image"] is None
    assert Collection.objects.get(pk=made["id"]).image_file_names() == []
    assert set(second) <= set(PendingFileRemoval.objects.values_list("name", flat=True))


def test_removing_when_there_is_no_image_is_harmless(boss):
    made = create(boss)

    assert boss.delete(image_url(made)).status_code == HTTPStatus.OK


def test_image_changes_are_written_to_the_audit_log(boss):
    made = create(boss)
    before = AuditEntry.objects.filter(action="collection_edited").count()

    upload(boss, made)
    boss.delete(image_url(made))

    assert AuditEntry.objects.filter(action="collection_edited").count() == before + 2


def test_deleting_a_collection_schedules_its_image_files_for_removal(boss):
    made = create(boss)
    upload(boss, made)
    collection_admin.process_image(made["id"])
    names = Collection.objects.get(pk=made["id"]).image_file_names()

    assert boss.delete(f"{COLLECTIONS}/{made['id']}").status_code == 204

    assert set(names) <= set(PendingFileRemoval.objects.values_list("name", flat=True))


# --- Staff ----------------------------------------------------------------------------


def test_staff_manage_the_image_of_a_draft(staff):
    made = create(staff)

    assert upload(staff, made).status_code == HTTPStatus.OK
    assert staff.patch(image_url(made), {"alt_text": "x"}).status_code == 200
    assert staff.delete(image_url(made)).status_code == HTTPStatus.OK


def test_staff_cannot_change_the_image_of_a_published_collection(staff, boss):
    made = published(boss)
    upload(boss, made)

    for response in (
        upload(staff, made, colour=(5, 5, 5)),
        staff.patch(image_url(made), {"alt_text": "x"}),
        staff.delete(image_url(made)),
    ):
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert response.json()["code"] == "collection_owner_only"
    assert Collection.objects.get(pk=made["id"]).image_original


def test_staff_cannot_change_the_image_of_a_pending_or_unpublished_collection(
    staff,
    boss,
):
    pending = create(staff, name="Bekleyen")
    staff.post(f"{COLLECTIONS}/{pending['id']}/submit")
    off = published(boss)
    boss.post(f"{COLLECTIONS}/{off['id']}/unpublish")

    assert upload(staff, pending).status_code == HTTPStatus.CONFLICT
    assert upload(staff, off).status_code == HTTPStatus.FORBIDDEN


def test_the_owner_cannot_change_the_image_while_a_pending_change_exists(staff, boss):
    made = published(boss)
    staff.patch(f"{COLLECTIONS}/{made['id']}", {"description": "Yeni"})

    response = upload(boss, made)

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "collection_has_pending_change"


def test_the_owner_can_change_the_image_of_a_published_collection(boss):
    made = published(boss)

    assert upload(boss, made).status_code == HTTPStatus.OK


def test_the_image_endpoints_need_sign_in():
    client = Client()
    url = f"{COLLECTIONS}/1/image"

    assert client.post(url, {}).status_code == HTTPStatus.UNAUTHORIZED
    assert client.delete(url).status_code == HTTPStatus.UNAUTHORIZED


# --- the Storefront -------------------------------------------------------------------


def test_the_page_shows_the_cover_image_only_once_it_is_ready(boss):
    made = published(boss)
    assert store_page().json()["image"] is None

    upload(boss, made, size=(1800, 1200))
    assert store_page().json()["image"] is None  # still processing

    collection_admin.process_image(made["id"])

    image = store_page().json()["image"]
    assert [s["width"] for s in image["sizes"]] == [400, 800, 1600]
    assert image["alt"] == "Editörün seçimi"  # no alt text: falls back to the name

    boss.patch(image_url(made), {"alt_text": "Sonbahar"})
    assert store_page().json()["image"]["alt"] == "Sonbahar"


def test_removing_the_image_takes_it_off_the_page(boss):
    made = published(boss)
    upload(boss, made)
    collection_admin.process_image(made["id"])
    assert store_page().json()["image"] is not None

    boss.delete(image_url(made))

    assert store_page().json()["image"] is None


def test_a_failed_image_is_not_shown_and_does_not_hide_the_page(boss, monkeypatch):
    made = published(boss)
    upload(boss, made)
    monkeypatch.setattr(
        "store_backend.services.media.make_derivatives",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bozuk")),
    )

    collection_admin.process_image(made["id"])

    assert boss.get(f"{COLLECTIONS}/{made['id']}").json()["image"]["status"] == "failed"
    page = store_page()
    assert page.status_code == HTTPStatus.OK
    assert page.json()["image"] is None


def test_the_image_does_not_make_a_hidden_collection_public(boss):
    made = create(boss)  # a draft
    upload(boss, made)
    collection_admin.process_image(made["id"])

    assert store_page().status_code == HTTPStatus.NOT_FOUND
