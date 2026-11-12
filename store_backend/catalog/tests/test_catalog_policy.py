"""Owner-only catalog policy and view-model checks at the service level."""

from __future__ import annotations

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import ForbiddenError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import catalog_policy
from store_backend.services import catalog_views
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

OWNER_ONLY = {"cost_price", "supplier", "supplier_code"}


@pytest.fixture
def owner():
    return staff_member("Owner")


@pytest.fixture
def staff():
    return staff_member("Staff")


@pytest.fixture
def product():
    return catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Giyim"),
        shelf_price="100.00",
        cost_price="60.00",
        supplier=catalog.create_supplier("Ted", "T1"),
        supplier_code="X-1",
    )


@pytest.mark.parametrize("field", ["cost_price", "supplier_id", "supplier_code"])
def test_staff_input_of_owner_fields_is_forbidden_even_to_clear(staff, field):
    with pytest.raises(ForbiddenError):
        catalog_policy.require_owner_fields_allowed(staff, {field: None})


def test_owner_input_of_owner_fields_is_allowed(owner):
    catalog_policy.require_owner_fields_allowed(
        owner,
        {"cost_price": "1.00", "supplier_id": 1, "supplier_code": ""},
    )


def test_staff_input_of_other_fields_is_allowed(staff):
    catalog_policy.require_owner_fields_allowed(staff, {"title": "x"})


def test_output_omits_owner_fields_for_staff_only(owner, staff, product):
    payload = catalog_views.product_dict(staff, product)
    assert not OWNER_ONLY & payload.keys()
    assert catalog_views.product_dict(owner, product).keys() >= OWNER_ONLY


@pytest.mark.parametrize(
    ("current", "new", "staff_ok"),
    [
        (ProductStatus.DRAFT, ProductStatus.PUBLISHED, True),
        (ProductStatus.PUBLISHED, ProductStatus.DRAFT, True),
        (ProductStatus.DRAFT, ProductStatus.ARCHIVED, False),
        (ProductStatus.ARCHIVED, ProductStatus.DRAFT, False),
    ],
)
def test_status_change_matrix(  # noqa: PLR0913, PLR0917
    owner,
    staff,
    product,
    current,
    new,
    staff_ok,
):
    product.profile.status = current
    catalog_policy.require_status_change_allowed(owner, product, new)
    if staff_ok:
        catalog_policy.require_status_change_allowed(staff, product, new)
    else:
        with pytest.raises(ForbiddenError):
            catalog_policy.require_status_change_allowed(staff, product, new)


def test_patch_product_rejects_staff_owner_fields_before_writing(staff, product):
    with pytest.raises(ForbiddenError):
        catalog.patch_product(staff, product.pk, {"title": "Yeni", "cost_price": "1"})
    product.refresh_from_db()
    assert product.title == "Elbise"


def test_patch_product_resolves_ids_and_returns_fresh_state(owner, product):
    brand = catalog.create_brand("Marka")
    updated = catalog.patch_product(owner, product.pk, {"brand_id": brand.pk})
    assert updated.profile.brand == brand


def test_patch_product_unknown_reference_is_422(owner, product):
    with pytest.raises(UnprocessableError):
        catalog.patch_product(owner, product.pk, {"brand_id": 999999})


def test_patch_product_missing_product_is_not_found(owner):
    with pytest.raises(NotFoundError):
        catalog.patch_product(owner, 999999, {"title": "x"})


def test_change_status_enforces_archive_policy(owner, staff, product):
    with pytest.raises(ForbiddenError):
        catalog.change_status(staff, product.pk, ProductStatus.ARCHIVED)
    archived = catalog.change_status(owner, product.pk, ProductStatus.ARCHIVED)
    assert archived.profile.status == ProductStatus.ARCHIVED
