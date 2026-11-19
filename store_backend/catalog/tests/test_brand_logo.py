# ruff: noqa: PLR2004
"""A Brand's optional logo: upload, replace, remove, the limits, and the Storefront."""

from __future__ import annotations

import io
from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from store_backend.catalog.models import Brand
from store_backend.catalog.models import PendingFileRemoval
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.catalog.tests.test_storefront_api import Shop
from store_backend.services import brand_logo
from store_backend.services import catalog
from store_backend.services import media
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

BRANDS = "/api/admin/brands"
STORE = "/api/store/products"


@pytest.fixture
def staff(staff_user, api_for):  # noqa: F811
    return api_for(staff_user)


@pytest.fixture
def brand():
    return catalog.create_brand("Nike")


def logo_url(brand):
    return f"{BRANDS}/{brand.pk}/logo"


def upload(client, brand, **kwargs):
    kwargs.setdefault("size", (400, 300))
    return post_file(client, logo_url(brand), make_file(**kwargs))


def listed(client, brand):
    rows = client.get(BRANDS).json()
    return next(r for r in rows if r["id"] == brand.pk)


def test_a_brand_has_no_logo_by_default(staff, brand):
    row = listed(staff, brand)

    assert row["logo_status"] == ""
    assert row["logo_url"] is None


def test_an_uploaded_logo_is_processing_until_its_derivatives_exist(staff, brand):
    response = upload(staff, brand)

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["logo_status"] == "processing"
    assert response.json()["logo_url"] is None

    brand_logo.process(brand.pk)

    ready = listed(staff, brand)
    assert ready["logo_status"] == "ready"
    assert ready["logo_url"].endswith(".webp")
    assert "/brands/" in ready["logo_url"]


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_png_jpeg_and_webp_are_accepted(staff, brand, fmt):
    assert upload(staff, brand, fmt=fmt).status_code == HTTPStatus.OK


def test_a_transparent_png_keeps_its_transparency(staff, brand):
    buf = io.BytesIO()
    Image.new("RGBA", (300, 300), (255, 0, 0, 0)).save(buf, "PNG")
    file = SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")
    assert post_file(staff, logo_url(brand), file).status_code == HTTPStatus.OK

    brand_logo.process(brand.pk)

    brand.refresh_from_db()
    with media.default_storage.open(brand.logo_derivatives[0]["name"]) as f:
        assert Image.open(f).mode == "RGBA"


def test_a_small_logo_is_accepted_but_not_a_tiny_one(staff, brand):
    assert upload(staff, brand, size=(200, 120)).status_code == HTTPStatus.OK

    tiny = upload(staff, brand, size=(150, 100))

    assert tiny.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "200" in tiny.json()["fields"]["file"][0]


def test_a_logo_over_2_mb_is_refused_and_leaves_the_brand_alone(staff, brand):
    big = SimpleUploadedFile(
        "logo.png",
        b"\x89PNG" + b"0" * (brand_logo.MAX_BYTES + 1),
        content_type="image/png",
    )

    response = post_file(staff, logo_url(brand), big)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["fields"]["file"] == [media.msg_size(brand_logo.MAX_BYTES)]
    assert listed(staff, brand)["logo_status"] == ""


def test_an_svg_is_refused(staff, brand):
    svg = SimpleUploadedFile(
        "logo.svg",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"/>',
        content_type="image/svg+xml",
    )

    response = post_file(staff, logo_url(brand), svg)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["fields"]["file"] == [media.MSG_FORMAT]


def test_product_photo_limits_are_unchanged(staff):
    assert media.MAX_BYTES == 15 * 1024 * 1024
    assert media.MIN_LONG_EDGE == 800


def test_replacing_and_removing_schedule_the_old_files_for_removal(staff, brand):
    upload(staff, brand, colour=(1, 2, 3))
    brand_logo.process(brand.pk)
    first = Brand.objects.get(pk=brand.pk).logo_file_names()
    assert first

    upload(staff, brand, colour=(9, 9, 9))
    queued = set(PendingFileRemoval.objects.values_list("name", flat=True))
    assert set(first) <= queued

    brand_logo.process(brand.pk)
    second = Brand.objects.get(pk=brand.pk).logo_file_names()
    removed = staff.delete(logo_url(brand))

    assert removed.status_code == HTTPStatus.OK
    assert removed.json()["logo_status"] == ""
    assert removed.json()["logo_url"] is None
    assert Brand.objects.get(pk=brand.pk).logo_file_names() == []
    assert set(second) <= set(PendingFileRemoval.objects.values_list("name", flat=True))


def test_removing_when_there_is_no_logo_is_harmless(staff, brand):
    assert staff.delete(logo_url(brand)).status_code == HTTPStatus.OK


def test_a_logo_that_cannot_be_processed_is_marked_failed(staff, brand, monkeypatch):
    upload(staff, brand)
    monkeypatch.setattr(
        media,
        "make_derivatives",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )

    brand_logo.process(brand.pk)

    row = listed(staff, brand)
    assert row["logo_status"] == "failed"
    assert row["logo_url"] is None
    assert "boom" in row["logo_error"]


def test_a_logo_replaced_while_processing_is_not_overwritten(staff, brand, monkeypatch):
    upload(staff, brand, colour=(1, 2, 3))
    real = media.make_derivatives

    def replaced_midway(original, folder, written):
        upload(staff, brand, colour=(9, 9, 9))  # a newer upload lands meanwhile
        return real(original, folder, written)

    monkeypatch.setattr(media, "make_derivatives", replaced_midway)
    brand_logo.process(brand.pk)

    assert listed(staff, brand)["logo_status"] == "processing"


def test_uploading_needs_the_manage_brands_permission(brand):
    response = Client().post(
        logo_url(brand),
        {"file": make_file(size=(400, 300))},
    )

    assert response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


def test_an_unknown_brand_is_not_found(staff):
    response = post_file(staff, f"{BRANDS}/99999/logo", make_file(size=(400, 300)))

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_deleting_a_brand_schedules_its_logo_files_for_removal(owner, api_for, brand):  # noqa: F811
    boss = api_for(owner, enrol=True)
    upload(boss, brand)
    brand_logo.process(brand.pk)
    names = Brand.objects.get(pk=brand.pk).logo_file_names()

    assert boss.delete(f"{BRANDS}/{brand.pk}").status_code == HTTPStatus.NO_CONTENT

    assert set(names) <= set(PendingFileRemoval.objects.values_list("name", flat=True))


# --- Storefront -----------------------------------------------------------------------


def with_logo(brand, *, ready=True):
    brand_logo.upload(brand, make_file(size=(400, 300)))
    if ready:
        brand_logo.process(brand.pk)
    return Brand.objects.get(pk=brand.pk)


def test_the_product_and_the_brand_facet_carry_the_logo_once_ready(client, brand):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 1)], brand=brand)
    slug = client.get(STORE).json()["items"][0]["slug"]
    ready = with_logo(brand)

    detail = client.get(f"{STORE}/{slug}").json()
    facet = client.get(STORE).json()["facets"]["brands"]

    assert detail["brand"]["logo_url"] == ready.logo_url()
    assert detail["brand"]["logo_url"].endswith(".webp")
    assert facet[0]["logo_url"] == ready.logo_url()


def test_the_storefront_shows_no_logo_while_it_is_processing_or_when_removed(
    client,
    staff,
    brand,
):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 1)], brand=brand)
    slug = client.get(STORE).json()["items"][0]["slug"]
    with_logo(brand, ready=False)

    assert client.get(f"{STORE}/{slug}").json()["brand"]["logo_url"] is None
    assert client.get(STORE).json()["facets"]["brands"][0]["logo_url"] is None

    brand_logo.process(brand.pk)
    staff.delete(logo_url(brand))

    assert client.get(f"{STORE}/{slug}").json()["brand"]["logo_url"] is None


def test_a_product_without_a_brand_still_has_none(client):
    shop = Shop()
    shop.product("Elbise", [(shop.black, "M", 1)])
    slug = client.get(STORE).json()["items"][0]["slug"]

    assert client.get(f"{STORE}/{slug}").json()["brand"] is None
