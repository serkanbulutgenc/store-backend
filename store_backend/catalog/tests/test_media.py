# ruff: noqa: PLR2004, SLF001
from __future__ import annotations

import io
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from oscar.apps.catalogue.models import ProductClass
from PIL import Image

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog import tasks
from store_backend.catalog.models import PendingFileRemoval
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductStatus
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import media
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

BASE = "/api/admin"


def make_file(  # noqa: PLR0913
    size=(1200, 900),
    fmt="JPEG",
    name="foto.jpg",
    orientation=None,
    colour=(200, 30, 30),
    *,
    gps=False,
) -> SimpleUploadedFile:
    img = Image.new("RGB", size, colour)
    buf = io.BytesIO()
    kwargs = {}
    if orientation or gps:
        exif = Image.Exif()
        exif[0x0112] = orientation or 1
        exif[0x010F] = "SecretCamera"
        if gps:
            gps_ifd = exif.get_ifd(0x8825)
            gps_ifd[1] = "N"
            gps_ifd[2] = (36.0, 47.0, 30.0)
            gps_ifd[3] = "E"
            gps_ifd[4] = (34.0, 38.0, 0.0)
        kwargs["exif"] = exif
    img.save(buf, fmt, **kwargs)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")


@pytest.fixture
def actor(db):
    return staff_member("Staff")


@pytest.fixture
def colour():
    return catalog.create_colour("Siyah", "#000000")


@pytest.fixture
def product(colour):
    cat = catalog.create_category("Giyim")
    product = catalog.create_product(
        title="Yazlık Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cat,
        shelf_price="199.90",
    )
    catalog.create_variant(product, colour=colour, size="M", stock=3)
    return product


def upload_ready(actor, product, **kwargs) -> ProductPhoto:
    image = media.upload(actor, product, make_file(), **kwargs)
    media.process(image.pk)
    image.refresh_from_db()
    return image


# --- upload and processing ------------------------------------------------------


def test_upload_keeps_original_and_marks_image_processing(actor, product):
    image = media.upload(actor, product, make_file())
    assert image.status == ProductPhoto.Status.PROCESSING
    assert default_storage.exists(image.original)
    assert image.width == 1200
    assert image.height == 900
    assert image.is_primary
    assert image.sort_order == 1


def test_processing_makes_webp_derivatives_with_hashed_names(actor, product):
    image = upload_ready(actor, product)
    assert image.status == ProductPhoto.Status.READY
    assert [d["width"] for d in image.derivatives] == [400, 800]
    for d in image.derivatives:
        assert d["name"].endswith(f"-{d['width']}.webp")
        with default_storage.open(d["name"]) as f:
            opened = Image.open(f)
            assert opened.format == "WEBP"
            assert opened.width == d["width"]
    assert default_storage.exists(image.original)  # the original stays


def test_large_image_gets_all_three_sizes(actor, product):
    image = media.upload(actor, product, make_file(size=(2400, 1600)))
    media.process(image.pk)
    image.refresh_from_db()
    assert [d["width"] for d in image.derivatives] == [400, 800, 1600]


def test_same_content_gives_same_name_and_new_content_a_new_one(actor, product):
    a = upload_ready(actor, product)
    b = upload_ready(actor, product)
    assert a.derivatives == b.derivatives
    c = media.upload(actor, product, make_file(colour=(1, 2, 3)))
    media.process(c.pk)
    c.refresh_from_db()
    assert c.derivatives[0]["name"] != a.derivatives[0]["name"]


def test_exif_is_stripped_and_orientation_applied(actor, product):
    image = media.upload(actor, product, make_file(size=(1200, 900), orientation=6))
    media.process(image.pk)
    image.refresh_from_db()
    biggest = image.derivatives[-1]
    with default_storage.open(biggest["name"]) as f:
        opened = Image.open(f)
        opened.load()
        assert len(opened.getexif()) == 0
        assert b"SecretCamera" not in opened.info.get("exif", b"")
        assert opened.height > opened.width  # rotated upright (1200x900 -> 900x1200)


def test_media_url_goes_through_one_function(settings, actor, product):
    settings.MEDIA_URL = "/media/"
    image = upload_ready(actor, product)
    name = image.derivatives[0]["name"]
    assert media.media_url(name) == f"/media/{name}"
    assert image.derivative_urls()[0] == {
        "width": 400,
        "url": media.media_url(name),
    }


def test_processing_failure_leaves_original_usable_and_marks_failed(actor, product):
    image = media.upload(actor, product, make_file())
    with default_storage.open(image.original, "wb") as f:
        f.write(b"not an image any more")
    media.process(image.pk)  # must not raise
    image.refresh_from_db()
    assert image.status == ProductPhoto.Status.FAILED
    assert image.error
    assert default_storage.exists(image.original)
    assert image.derivatives == []


def test_celery_task_runs_processing(
    actor,
    product,
    django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        image = media.upload(actor, product, make_file())
    image.refresh_from_db()
    assert image.status == ProductPhoto.Status.READY
    assert tasks.process_product_image.name


# --- validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("upload", "field_message"),
    [
        (SimpleUploadedFile("a.txt", b"hello"), "JPEG, PNG veya WebP"),
        (make_file(fmt="GIF", name="a.gif"), "JPEG, PNG veya WebP"),
        (make_file(size=(700, 500)), "800"),
    ],
)
def test_rejects_unsupported_or_small_files(actor, product, upload, field_message):
    with pytest.raises(UnprocessableError) as exc:
        media.upload(actor, product, upload)
    assert field_message in exc.value.fields["file"][0]
    assert not ProductPhoto.objects.exists()


def test_rejects_files_over_15_mb(actor, product, settings):
    big = SimpleUploadedFile("big.jpg", b"x" * (media.MAX_BYTES + 1))
    with pytest.raises(UnprocessableError) as exc:
        media.upload(actor, product, big)
    assert "15 MB" in exc.value.fields["file"][0]


def test_accepts_png_and_webp(actor, product):
    media.upload(actor, product, make_file(fmt="PNG", name="a.png"))
    media.upload(actor, product, make_file(fmt="WEBP", name="a.webp"))
    assert ProductPhoto.objects.count() == 2


def test_twelve_images_per_product(actor, product):
    for _ in range(media.MAX_IMAGES):
        media.upload(actor, product, make_file(size=(800, 800)))
    with pytest.raises(UnprocessableError) as exc:
        media.upload(actor, product, make_file())
    assert "12" in exc.value.fields["file"][0]


def test_long_edge_counts_for_minimum(actor, product):
    media.upload(actor, product, make_file(size=(500, 800)))  # 800 on the long edge


def make_heic(size=(1200, 900), *, gps=False) -> SimpleUploadedFile:
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    if gps:
        exif.get_ifd(0x8825)[1] = "N"
        exif.get_ifd(0x8825)[2] = (36.0, 47.0, 30.0)
    Image.new("RGB", size, (20, 90, 200)).save(buf, "HEIF", exif=exif)
    return SimpleUploadedFile("IMG_1.HEIC", buf.getvalue())


def test_heic_is_supported():
    assert media.heic_supported()


def test_heic_upload_is_kept_and_converted_to_webp(actor, product):
    image = media.upload(actor, product, make_heic())
    assert image.original.endswith(".heic")
    media.process(image.pk)
    image.refresh_from_db()
    assert image.status == ProductPhoto.Status.READY
    with default_storage.open(image.derivatives[0]["name"]) as f:
        assert Image.open(f).format == "WEBP"


def test_heic_with_gps_is_stored_as_jpeg_without_it(actor, product):
    image = media.upload(actor, product, make_heic(gps=True))
    assert image.original.endswith(".jpg")
    stored = Image.open(io.BytesIO(stored_original(image)))
    assert stored.format == "JPEG"
    assert not stored.getexif().get_ifd(0x8825)


def test_corrupt_heic_is_rejected(actor, product):
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    with pytest.raises(UnprocessableError) as exc:
        media.upload(actor, product, SimpleUploadedFile("IMG_1.HEIC", heic))
    assert "bozuk" in exc.value.fields["file"][0]
    assert not ProductPhoto.objects.exists()


# --- alt text, colour, primary, order ------------------------------------------


def test_alt_text_defaults_to_product_name_and_colour(actor, product, colour):
    with_colour = media.upload(actor, product, make_file(), colour=colour)
    plain = media.upload(actor, product, make_file())
    assert with_colour.effective_alt_text == "Yazlık Elbise Siyah"
    assert plain.effective_alt_text == "Yazlık Elbise"
    media.update(actor, with_colour, alt_text="Önden görünüm")
    assert with_colour.effective_alt_text == "Önden görünüm"
    media.update(actor, with_colour, alt_text="")
    assert with_colour.effective_alt_text == "Yazlık Elbise Siyah"


def test_only_one_primary_and_first_upload_is_primary(actor, product):
    first = media.upload(actor, product, make_file())
    second = media.upload(actor, product, make_file())
    assert (first.is_primary, second.is_primary) == (True, False)
    media.update(actor, second, is_primary=True)
    first.refresh_from_db()
    assert not first.is_primary
    assert ProductPhoto.objects.filter(product=product, is_primary=True).count() == 1


def test_deleting_primary_promotes_next_image(actor, product):
    first = media.upload(actor, product, make_file())
    second = media.upload(actor, product, make_file())
    media.delete(actor, first)
    second.refresh_from_db()
    assert second.is_primary


def test_reorder_requires_exactly_the_products_images(actor, product):
    a, b, c = (media.upload(actor, product, make_file()) for _ in range(3))
    media.reorder(actor, product, [c.pk, a.pk, b.pk])
    ordered = list(product.photos.order_by("sort_order").values_list("pk", flat=True))
    assert ordered == [c.pk, a.pk, b.pk]
    with pytest.raises(UnprocessableError):
        media.reorder(actor, product, [a.pk, b.pk])


def test_colour_must_exist_in_update(actor, product, colour):
    image = media.upload(actor, product, make_file())
    media.update(actor, image, colour=colour)
    assert image.colour == colour
    media.update(actor, image, colour=None)
    assert image.colour is None


# --- publish and delete rules ------------------------------------------------------


def test_cannot_publish_without_a_ready_image(actor, product):
    with pytest.raises(UnprocessableError):
        catalog.set_status(product, ProductStatus.PUBLISHED)
    image = media.upload(actor, product, make_file())  # still processing
    with pytest.raises(UnprocessableError):
        catalog.set_status(product, ProductStatus.PUBLISHED)
    media.process(image.pk)
    catalog.set_status(product, ProductStatus.PUBLISHED)


def test_last_image_of_published_product_cannot_be_deleted(actor, product):
    only = upload_ready(actor, product)
    catalog.set_status(product, ProductStatus.PUBLISHED)
    with pytest.raises(ConflictError) as exc:
        media.delete(actor, only)
    assert exc.value.code == "last_image"
    second = upload_ready(actor, product)
    media.delete(actor, second)
    assert product.photos.count() == 1


def test_draft_product_may_lose_its_last_image(actor, product):
    only = upload_ready(actor, product)
    media.delete(actor, only)
    assert not product.photos.exists()


def test_delete_removes_files_only_after_the_grace_period(actor, product, settings):
    image = upload_ready(actor, product)
    names = [image.original, *(d["name"] for d in image.derivatives)]
    media.delete(actor, image)
    assert PendingFileRemoval.objects.count() == len(names)
    assert media.purge_removed_files() == 0
    assert all(default_storage.exists(n) for n in names)
    PendingFileRemoval.objects.update(
        remove_after=timezone.now() - timedelta(minutes=1),
    )
    assert media.purge_removed_files() == len(names)
    assert not any(default_storage.exists(n) for n in names)
    assert not PendingFileRemoval.objects.exists()


def test_purge_ignores_files_already_gone(actor, product):
    PendingFileRemoval.objects.create(
        name="products/nope.webp",
        remove_after=timezone.now(),
    )
    assert media.purge_removed_files() == 1


def test_a_file_still_used_by_another_image_is_not_removed(actor, product):
    a = upload_ready(actor, product)
    b = upload_ready(actor, product)  # identical content, same derivative names
    media.delete(actor, a)
    PendingFileRemoval.objects.update(
        remove_after=timezone.now() - timedelta(minutes=1),
    )
    media.purge_removed_files()
    for d in b.derivatives:
        assert default_storage.exists(d["name"])


def test_upload_and_delete_are_audited(actor, product):
    image = media.upload(actor, product, make_file())
    media.delete(actor, image)
    actions = list(AuditEntry.objects.values_list("action", flat=True))
    assert audit.Action.PHOTO_UPLOAD in actions
    assert audit.Action.PHOTO_DELETE in actions
    entry = AuditEntry.objects.get(action=audit.Action.PHOTO_UPLOAD)
    assert entry.actor == actor
    assert "Yazlık Elbise" in entry.summary


def test_removal_names_are_scheduled_even_for_cascade_deletes(actor, product):
    upload_ready(actor, product)
    product.delete()
    assert PendingFileRemoval.objects.exists()


def test_stray_file_in_storage_is_untouched(actor, product):
    default_storage.save("products/stray.txt", ContentFile(b"hi"))
    upload_ready(actor, product)
    assert default_storage.exists("products/stray.txt")


# --- API ------------------------------------------------------------------------


@pytest.fixture
def staff_api(api_for, actor):
    return api_for(actor)


def post_file(api, url, upload, **fields):
    return api.client.post(
        url,
        {"file": upload, **fields},
        headers=api.headers,
    )


def test_api_upload_list_patch_order_delete(staff_api, product, colour):
    url = f"{BASE}/products/{product.pk}/images"
    first = post_file(staff_api, url, make_file(), colour_id=colour.pk)
    assert first.status_code == HTTPStatus.CREATED, first.content
    body = first.json()
    assert body["status"] == "processing"
    assert body["is_primary"] is True
    assert body["colour"]["id"] == colour.pk
    assert body["alt_text"] == "Yazlık Elbise Siyah"
    assert body["original_url"].endswith(".jpg")
    second = post_file(staff_api, url, make_file()).json()

    listed = staff_api.get(url).json()
    assert [i["id"] for i in listed] == [body["id"], second["id"]]

    patched = staff_api.client.patch(
        f"{BASE}/images/{second['id']}",
        {"alt_text": "Arkadan", "is_primary": True, "colour_id": None},
        content_type="application/json",
        headers=staff_api.headers,
    )
    assert patched.status_code == HTTPStatus.OK, patched.content
    assert patched.json()["alt_text"] == "Arkadan"
    assert patched.json()["is_primary"] is True

    ordered = staff_api.post(
        f"{url}/order",
        {"ids": [second["id"], body["id"]]},
    )
    assert ordered.status_code == HTTPStatus.OK, ordered.content
    assert [i["id"] for i in ordered.json()] == [second["id"], body["id"]]

    deleted = staff_api.client.delete(
        f"{BASE}/images/{second['id']}",
        headers=staff_api.headers,
    )
    assert deleted.status_code == HTTPStatus.NO_CONTENT


def test_api_rejects_bad_upload_with_envelope(staff_api, product):
    response = post_file(
        staff_api,
        f"{BASE}/products/{product.pk}/images",
        SimpleUploadedFile("a.txt", b"hello"),
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "file" in response.json()["fields"]


def test_api_unknown_colour_is_rejected(staff_api, product):
    response = post_file(
        staff_api,
        f"{BASE}/products/{product.pk}/images",
        make_file(),
        colour_id=9999,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_api_requires_authentication(client, product):
    response = client.post(
        f"{BASE}/products/{product.pk}/images",
        {"file": make_file()},
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_api_publish_needs_an_image_then_works(staff_api, product, actor):
    url = f"{BASE}/products/{product.pk}/status"
    blocked = staff_api.post(url, {"status": "published"})
    assert blocked.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    upload_ready(actor, product)
    assert staff_api.post(url, {"status": "published"}).status_code == HTTPStatus.OK


def test_api_cannot_delete_last_image_of_published_product(staff_api, product, actor):
    image = upload_ready(actor, product)
    catalog.set_status(product, ProductStatus.PUBLISHED)
    response = staff_api.client.delete(
        f"{BASE}/images/{image.pk}",
        headers=staff_api.headers,
    )
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "last_image"


# --- review fixes -------------------------------------------------------------


def stored_original(image) -> bytes:
    with default_storage.open(image.original) as f:
        return f.read()


@pytest.mark.parametrize(("fmt", "name"), [("JPEG", "a.jpg"), ("PNG", "a.png")])
def test_gps_is_stripped_from_the_stored_original(actor, product, fmt, name):
    upload = make_file(fmt=fmt, name=name, gps=True, orientation=6)
    assert Image.open(io.BytesIO(upload.read())).getexif().get_ifd(0x8825)
    upload.seek(0)
    image = media.upload(actor, product, upload)
    stored = Image.open(io.BytesIO(stored_original(image)))
    exif = stored.getexif()
    assert not exif.get_ifd(0x8825)
    assert exif.get(0x0112) == 6  # the rest of the metadata stays
    assert stored.size == (1200, 900)
    assert image.width == 900  # orientation 6 swaps the reported size


def test_original_without_gps_is_stored_byte_for_byte(actor, product):
    upload = make_file(orientation=6)
    raw = upload.read()
    upload.seek(0)
    image = media.upload(actor, product, upload)
    assert stored_original(image) == raw


def test_derivatives_finished_after_a_delete_are_still_scheduled_for_removal(
    actor,
    product,
    monkeypatch,
):
    image = media.upload(actor, product, make_file())
    real_clean = media._clean

    def delete_then_clean(img):
        ProductPhoto.objects.filter(pk=image.pk).delete()  # deleted mid-processing
        return real_clean(img)

    monkeypatch.setattr(media, "_clean", delete_then_clean)
    media.process(image.pk)
    pending = set(PendingFileRemoval.objects.values_list("name", flat=True))
    on_disk = {n for n in default_storage.listdir("products")[1] if n.endswith(".webp")}
    assert on_disk
    assert {f"products/{n}" for n in on_disk} <= pending


def test_failed_upload_leaves_no_original_behind(actor, product, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        media.upload(actor, product, make_file())
    files = (
        default_storage.listdir("products")[1]
        if default_storage.exists("products")
        else []
    )
    assert files == []


def test_edits_and_reorders_are_audited(actor, product, colour):
    a = media.upload(actor, product, make_file())
    b = media.upload(actor, product, make_file())
    media.update(actor, a, colour=colour, alt_text="Önden")
    media.reorder(actor, product, [b.pk, a.pk])
    edits = AuditEntry.objects.filter(action=audit.Action.PHOTO_EDIT)
    assert edits.count() == 2
    first = edits.order_by("pk")[0]
    assert first.after is not None
    assert first.actor == actor
    assert first.after["alt_text"] == "Önden"


def test_deleting_primary_prefers_a_ready_successor(actor, product):
    primary = upload_ready(actor, product)
    failed = media.upload(actor, product, make_file())
    ProductPhoto.objects.filter(pk=failed.pk).update(status=ProductPhoto.Status.FAILED)
    ready = upload_ready(actor, product)
    media.delete(actor, primary)
    ready.refresh_from_db()
    failed.refresh_from_db()
    assert ready.is_primary
    assert not failed.is_primary


def test_delete_reads_the_published_status_after_taking_the_lock(actor, product):
    only = upload_ready(actor, product)
    stale = ProductPhoto.objects.select_related("product").get(pk=only.pk)
    catalog.set_status(product, ProductStatus.PUBLISHED)  # after `stale` was loaded
    with pytest.raises(ConflictError):
        media.delete(actor, stale)
