"""Images placed inside a Product Description: stateless uploads, no Photo rows."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.tests.test_media import make_file
from store_backend.catalog.tests.test_media import post_file
from store_backend.conftest import make_verified_user
from store_backend.services import media
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

URL = "/api/admin/description-images"
GPS_TAG = 0x8825  # the EXIF pointer to location data


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


def test_staff_uploads_an_image_and_gets_its_public_url(staff_api):
    response = post_file(staff_api, URL, make_file())

    assert response.status_code == HTTPStatus.CREATED, response.content
    url = response.json()["url"]
    assert url.endswith(".jpg")
    assert url.startswith(settings.MEDIA_URL)
    name = url.removeprefix(settings.MEDIA_URL)
    assert name.startswith("description/")
    assert default_storage.exists(name)


def test_a_small_image_is_accepted(staff_api):
    response = post_file(staff_api, URL, make_file(size=(300, 200)))

    assert response.status_code == HTTPStatus.CREATED, response.content


def stored_size(response) -> tuple[int, int]:
    name = response.json()["url"].removeprefix(settings.MEDIA_URL)
    with default_storage.open(name) as f:
        return Image.open(f).size


def test_a_wide_image_is_scaled_down_to_1600_pixels_keeping_its_ratio(staff_api):
    response = post_file(staff_api, URL, make_file(size=(2400, 1200)))

    assert stored_size(response) == (1600, 800)
    assert stored_size(post_file(staff_api, URL, make_file(size=(1600, 900)))) == (
        1600,
        900,
    )


def stored_exif(response) -> dict:
    name = response.json()["url"].removeprefix(settings.MEDIA_URL)
    with default_storage.open(name) as f:
        return dict(Image.open(f).getexif())


def test_location_data_is_removed_whether_or_not_the_image_is_scaled(staff_api):
    kept_size = post_file(staff_api, URL, make_file(size=(1000, 800), gps=True))
    scaled = post_file(staff_api, URL, make_file(size=(2400, 1200), gps=True))

    for response in (kept_size, scaled):
        assert GPS_TAG not in stored_exif(response)


def test_only_jpeg_png_and_webp_images_are_accepted(staff_api):
    response = post_file(staff_api, URL, SimpleUploadedFile("a.txt", b"hello"))

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "file" in response.json()["fields"]


def test_a_user_without_photo_permission_cannot_upload(api_for):
    api = api_for(make_verified_user(is_staff=True))  # in no role group

    response = post_file(api, URL, make_file())

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_the_owner_can_upload_too(owner_api):
    response = post_file(owner_api, URL, make_file())

    assert response.status_code == HTTPStatus.CREATED, response.content


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_images_are_accepted(staff_api, fmt):
    upload = make_file(fmt=fmt, name=f"foto.{fmt.lower()}")

    response = post_file(staff_api, URL, upload)

    assert response.status_code == HTTPStatus.CREATED, response.content
    assert stored_size(response) == (1200, 900)


def test_a_file_that_is_not_really_an_image_is_refused(staff_api):
    upload = SimpleUploadedFile("foto.jpg", b"not an image at all")

    response = post_file(staff_api, URL, upload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "file" in response.json()["fields"]


def test_a_file_over_15_mb_is_refused(staff_api):
    upload = SimpleUploadedFile("foto.jpg", b"0" * (media.MAX_BYTES + 1))

    response = post_file(staff_api, URL, upload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert media.MSG_SIZE in response.json()["fields"]["file"]


def test_no_metadata_survives_whether_or_not_the_image_is_scaled(staff_api):
    for size in ((1000, 800), (2400, 1200)):
        response = post_file(staff_api, URL, make_file(size=size, gps=True))

        assert stored_exif(response) == {}


def test_an_upload_creates_no_photo_row(staff_api):
    post_file(staff_api, URL, make_file())

    assert not ProductPhoto.objects.exists()
