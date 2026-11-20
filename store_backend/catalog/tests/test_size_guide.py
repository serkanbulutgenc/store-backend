"""Size guide (per Product class, optional per-Product override) and the Fit note."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

CLASSES = "/api/admin/product-classes"
PRODUCTS = "/api/admin/products"
STORE = "/api/store/products"

TABLE = "| Beden | Göğüs |\n|---|---|\n| S | 88 |\n| M | 92 |"


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


def make_product(title="Elbise", klass="Giyim", *, size="M"):
    pclass = ProductClass.objects.get(name=klass)
    product = catalog.create_product(
        title=title,
        product_class=pclass,
        category=catalog.create_category(f"{title} kategorisi"),
        shelf_price="100.00",
    )
    catalog.create_variant(
        product,
        colour=catalog.create_colour(f"{title} rengi", "#000000"),
        size=size,
        stock=5,
    )
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )
    return product


def class_id(name="Giyim") -> int:
    return ProductClass.objects.get(name=name).pk


def storefront(product) -> dict:
    slug = product.profile.slug
    response = Client().get(f"{STORE}/{slug}")
    assert response.status_code == HTTPStatus.OK, response.content
    return response.json()


# --- the class guide ------------------------------------------------------------------


def test_a_class_guide_written_in_the_dashboard_shows_on_the_storefront(staff_api):
    product = make_product()

    response = staff_api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": TABLE})

    assert response.status_code == HTTPStatus.OK, response.content
    assert storefront(product)["size_guide"] == TABLE


# --- the Product override -------------------------------------------------------------

OVERRIDE = "| Beden | Bel |\n|---|---|\n| M | 70 |"


def test_a_product_override_wins_and_removing_it_falls_back_to_the_class_guide(
    staff_api,
):
    product = make_product()
    staff_api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": TABLE})

    set_ = staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": OVERRIDE})
    assert set_.status_code == HTTPStatus.OK, set_.content
    assert storefront(product)["size_guide"] == OVERRIDE

    cleared = staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": None})
    assert cleared.status_code == HTTPStatus.OK, cleared.content
    assert storefront(product)["size_guide"] == TABLE


# --- when no guide applies ------------------------------------------------------------


def test_a_product_with_neither_guide_has_none(staff_api):
    product = make_product()

    assert storefront(product)["size_guide"] is None


def test_a_one_size_product_hides_the_guide_even_when_one_is_written(staff_api):
    product = make_product("Çanta", klass="Aksesuar", size="Tek Ebat")
    staff_api.put(f"{CLASSES}/{class_id('Aksesuar')}/size-guide", {"text": TABLE})
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": OVERRIDE})

    assert storefront(product)["size_guide"] is None


def test_listing_payloads_do_not_carry_the_guide_or_the_fit_note(staff_api):
    product = make_product()
    staff_api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": TABLE})
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": "Bir beden büyük alın"})

    items = Client().get(STORE).json()["items"]

    assert items
    assert all("size_guide" not in i and "fit_note" not in i for i in items)


# --- the Fit note ---------------------------------------------------------------------


def test_a_fit_note_shows_on_the_storefront_and_can_be_removed(staff_api):
    product = make_product()

    saved = staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": "Dar kalıp"})
    assert saved.status_code == HTTPStatus.OK, saved.content
    assert saved.json()["fit_note"] == "Dar kalıp"
    assert storefront(product)["fit_note"] == "Dar kalıp"

    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": ""})
    assert storefront(product)["fit_note"] == ""


@pytest.mark.parametrize(
    "note",
    ["iki\nsatır", "x" * 121],
    ids=["more than one line", "too long"],
)
def test_a_fit_note_must_be_one_short_line(staff_api, note):
    product = make_product()

    response = staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": note})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "fit_note" in response.json()["fields"]


# --- who may edit ---------------------------------------------------------------------


def test_owner_and_staff_may_write_guides_and_the_fit_note(owner_api, staff_api):
    product = make_product()

    for api in (owner_api, staff_api):
        assert (
            api.put(
                f"{CLASSES}/{class_id()}/size-guide",
                {"text": TABLE},
            ).status_code
            == HTTPStatus.OK
        )
        assert api.get(f"{CLASSES}/{class_id()}/size-guide").json() == {"text": TABLE}
        patched = api.patch(
            f"{PRODUCTS}/{product.pk}",
            {"size_guide": OVERRIDE, "fit_note": "Dar kalıp"},
        )
        assert patched.status_code == HTTPStatus.OK, patched.content


def test_a_shopper_and_an_anonymous_visitor_may_not_edit(api_for):
    from store_backend.conftest import make_verified_user  # noqa: PLC0415
    from store_backend.staff.tests.conftest import Api  # noqa: PLC0415

    product = make_product()
    shopper = api_for(make_verified_user())

    for api in (shopper, Api()):
        responses = [
            api.get(f"{CLASSES}/{class_id()}/size-guide"),
            api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": TABLE}),
            api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": OVERRIDE}),
        ]
        assert [r.status_code for r in responses] == [
            HTTPStatus.UNAUTHORIZED if api is not shopper else HTTPStatus.FORBIDDEN,
        ] * 3, [r.content for r in responses]
    assert storefront(product)["size_guide"] is None


# --- the Audit log --------------------------------------------------------------------


def test_every_edit_is_written_to_the_audit_log(staff_api):
    from store_backend.staff.models import AuditEntry  # noqa: PLC0415

    product = make_product()

    staff_api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": TABLE})
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": OVERRIDE})
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": None})
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": "Dar kalıp"})

    actions = list(
        AuditEntry.objects.filter(
            action__in=["size_guide_edited", "fit_note_edited"],
        )
        .order_by("pk")
        .values_list("action", flat=True),
    )
    assert actions == [
        "size_guide_edited",  # the class guide
        "size_guide_edited",  # the override set
        "size_guide_edited",  # the override removed
        "fit_note_edited",
    ]
    class_entry = (
        AuditEntry.objects.filter(action="size_guide_edited").order_by("pk").first()
    )
    assert class_entry.actor is not None
    assert "Giyim" in class_entry.summary


def test_a_patch_that_changes_nothing_writes_no_audit_entry(staff_api):
    from store_backend.staff.models import AuditEntry  # noqa: PLC0415

    product = make_product()
    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": "Dar kalıp"})
    before = AuditEntry.objects.count()

    staff_api.patch(f"{PRODUCTS}/{product.pk}", {"fit_note": "Dar kalıp"})

    assert AuditEntry.objects.count() == before


# --- content: Description rules -------------------------------------------------------


def realistic_guide() -> str:
    """A full guide: 12 sizes by 10 measurements, three images and fitting tips."""
    columns = ["Beden", "Göğüs", "Bel", "Basen", "Omuz", "Kol", "Boy", "İç boy"]
    columns += ["Kalça", "Yaka"]
    head = "| " + " | ".join(columns) + " |\n|" + "---|" * len(columns)
    rows = [
        "| " + " | ".join(str(30 + n + c) for c in range(len(columns))) + " |"
        for n in range(12)
    ]
    images = "\n".join(
        f"![Ölçü {i}](/media/description/{'a' * 32}.jpg)" for i in range(3)
    )
    tips = "**Nasıl ölçülür?**\n\n" + "- Mezuranızı vücuda sıkmadan sarın.\n" * 8
    return "\n\n".join([head + "\n" + "\n".join(rows), images, tips])


def test_a_realistic_size_table_fits_the_length_cap(staff_api):
    guide = realistic_guide()
    product = make_product()

    class_saved = staff_api.put(f"{CLASSES}/{class_id()}/size-guide", {"text": guide})
    override_saved = staff_api.patch(f"{PRODUCTS}/{product.pk}", {"size_guide": guide})

    assert class_saved.status_code == HTTPStatus.OK, class_saved.content
    assert override_saved.status_code == HTTPStatus.OK, override_saved.content
    assert storefront(product)["size_guide"] == guide


def test_a_guide_over_the_description_cap_is_refused(staff_api):
    from store_backend.services.catalog import DESCRIPTION_MAX  # noqa: PLC0415

    product = make_product()
    too_long = "x" * (DESCRIPTION_MAX + 1)

    on_class = staff_api.put(
        f"{CLASSES}/{class_id()}/size-guide",
        {"text": too_long},
    )
    on_product = staff_api.patch(
        f"{PRODUCTS}/{product.pk}",
        {"size_guide": too_long},
    )

    assert on_class.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "text" in on_class.json()["fields"]
    assert on_product.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "size_guide" in on_product.json()["fields"]
