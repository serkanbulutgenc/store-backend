from __future__ import annotations

from decimal import Decimal
from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductClassProfile
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

DESI = 3

URL = "/api/admin/product-classes"


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


def body(**extra):
    return {
        "name": "Çanta",
        "size_scheme": "one_size",
        "default_kdv_rate": "20.00",
        "is_hygiene": False,
        "default_desi": DESI,
        **extra,
    }


def giyim() -> ProductClass:
    return ProductClass.objects.get(name="Giyim")


def add_product(pclass: ProductClass):
    category = catalog.create_category(f"Kat {pclass.pk}")
    return catalog.create_product(
        title="Elbise",
        product_class=pclass,
        category=category,
        shelf_price="199.90",
    )


def test_owner_creates_a_class_from_an_existing_size_scheme(owner_api):
    response = owner_api.post(URL, body())
    assert response.status_code == HTTPStatus.CREATED
    data = response.json()
    assert (data["name"], data["size_scheme"], data["sizes"]) == (
        "Çanta",
        "one_size",
        ["Tek Ebat"],
    )
    assert Decimal(data["default_kdv_rate"]) == Decimal(20)
    assert data["default_desi"] == DESI
    assert (
        ProductClassProfile.objects.get(product_class_id=data["id"]).default_desi
        == DESI
    )
    assert AuditEntry.objects.filter(summary__contains="oluşturuldu").count() == 1


def test_a_new_class_is_listed_and_usable_for_a_product(owner_api):
    class_id = owner_api.post(URL, body()).json()["id"]
    listed = {c["id"]: c for c in owner_api.get(URL).json()}
    assert listed[class_id]["product_count"] == 0
    product = add_product(ProductClass.objects.get(pk=class_id))
    assert product.product_class_id == class_id


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"name": "  "}, "name"),
        ({"size_scheme": "custom"}, "size_scheme"),
        ({"default_kdv_rate": "101"}, "default_kdv_rate"),
        ({"default_kdv_rate": "abc"}, "default_kdv_rate"),
        ({"default_desi": 0}, "default_desi"),
    ],
)
def test_invalid_input_is_refused(owner_api, extra, field):
    response = owner_api.post(URL, body(**extra))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in str(response.json())


def test_a_duplicate_name_is_refused_ignoring_case(owner_api):
    assert owner_api.post(URL, body(name="giyim")).status_code == HTTPStatus.CONFLICT


def test_delete_is_refused_while_a_product_uses_the_class(owner_api):
    add_product(giyim())
    response = owner_api.delete(f"{URL}/{giyim().pk}")
    assert response.status_code == HTTPStatus.CONFLICT
    assert ProductClass.objects.filter(name="Giyim").exists()


def test_an_unused_class_deletes_with_its_profile(owner_api):
    class_id = owner_api.post(URL, body()).json()["id"]
    assert owner_api.delete(f"{URL}/{class_id}").status_code == HTTPStatus.NO_CONTENT
    assert not ProductClass.objects.filter(pk=class_id).exists()
    assert not ProductClassProfile.objects.filter(product_class_id=class_id).exists()
    assert AuditEntry.objects.filter(summary__contains="silindi").count() == 1


def test_size_scheme_is_locked_once_the_class_has_products(owner_api):
    add_product(giyim())
    response = owner_api.patch(f"{URL}/{giyim().pk}", {"size_scheme": "shoes"})
    assert response.status_code == HTTPStatus.CONFLICT
    assert giyim().profile.size_scheme == "letters"
    # Sending the unchanged scheme alongside another change is fine.
    same = owner_api.patch(
        f"{URL}/{giyim().pk}",
        {"size_scheme": "letters", "default_desi": 5},
    )
    assert same.status_code == HTTPStatus.OK


def test_a_class_without_products_can_change_its_size_scheme(owner_api):
    class_id = owner_api.post(URL, body()).json()["id"]
    response = owner_api.patch(f"{URL}/{class_id}", {"size_scheme": "shoes"})
    assert response.status_code == HTTPStatus.OK
    assert response.json()["sizes"][0] == "35"


def test_a_kdv_change_applies_to_new_products_only(owner_api):
    old = giyim().profile.default_kdv_rate
    product = add_product(giyim())
    assert catalog.kdv_rate_for(product) == old
    response = owner_api.patch(f"{URL}/{giyim().pk}", {"default_kdv_rate": "20"})
    assert response.status_code == HTTPStatus.OK
    assert catalog.kdv_rate_for(product) == old
    assert catalog.kdv_rate_for(add_product(giyim())) == Decimal(20)


def test_rename_and_hygiene_flag_are_audited(owner_api):
    class_id = owner_api.post(URL, body()).json()["id"]
    response = owner_api.patch(
        f"{URL}/{class_id}",
        {"name": "Çantalar", "is_hygiene": True},
    )
    assert response.json()["name"] == "Çantalar"
    assert response.json()["is_hygiene"] is True
    assert AuditEntry.objects.filter(summary__contains="düzenlendi").count() == 1


def test_a_noop_patch_writes_no_audit_entry(owner_api):
    owner_api.patch(f"{URL}/{giyim().pk}", {"name": "Giyim"})
    assert not AuditEntry.objects.filter(summary__contains="düzenlendi").exists()


def test_unknown_class_is_404(owner_api):
    assert (
        owner_api.patch(f"{URL}/99999", {"name": "X"}).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert owner_api.delete(f"{URL}/99999").status_code == HTTPStatus.NOT_FOUND


def test_staff_may_list_but_not_create_edit_or_delete(staff_api):
    assert staff_api.get(URL).status_code == HTTPStatus.OK
    assert staff_api.post(URL, body()).status_code == HTTPStatus.FORBIDDEN
    assert (
        staff_api.patch(f"{URL}/1", {"name": "X"}).status_code == HTTPStatus.FORBIDDEN
    )
    assert staff_api.delete(f"{URL}/1").status_code == HTTPStatus.FORBIDDEN
