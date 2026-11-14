# ruff: noqa: PLR2004
"""Owner-only deletes, refused while referenced (ticket 71, ADR 0005)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.order.models import Line
from oscar.apps.order.models import Order

from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import PendingFileRemoval
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import SlugRedirect
from store_backend.catalog.models import Supplier
from store_backend.catalog.tests.conftest import add_ready_photo
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin"


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


def test_the_owner_deletes_an_unused_brand_and_it_is_audited(owner_api):
    brand = catalog.create_brand("Eski Marka")

    response = owner_api.delete(f"{ADMIN}/brands/{brand.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert owner_api.get(f"{ADMIN}/brands").json() == []
    assert AuditEntry.objects.filter(summary__contains="Eski Marka").count() == 1


def test_a_brand_used_by_products_is_refused_with_the_count(owner_api):
    brand = catalog.create_brand("Aura")
    category = catalog.create_category("Giyim")
    for title in ("Elbise", "Etek"):
        catalog.create_product(
            title=title,
            product_class=ProductClass.objects.get(name="Giyim"),
            category=category,
            shelf_price="100.00",
            brand=brand,
        )

    response = owner_api.delete(f"{ADMIN}/brands/{brand.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "brand_in_use"
    assert "2 ürün" in response.json()["message"]
    assert Brand.objects.filter(pk=brand.pk).exists()
    assert not AuditEntry.objects.filter(summary__contains="silindi").exists()


def test_staff_cannot_delete_a_brand(api_for):
    brand = catalog.create_brand("Aura")
    staff_api = api_for(staff_member("Staff"))

    response = staff_api.delete(f"{ADMIN}/brands/{brand.pk}")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert Brand.objects.filter(pk=brand.pk).exists()


def test_the_owner_deletes_an_unused_supplier_and_it_is_audited(owner_api):
    supplier = catalog.create_supplier("Eski Tedarikçi", "ET")

    response = owner_api.delete(f"{ADMIN}/suppliers/{supplier.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert owner_api.get(f"{ADMIN}/suppliers").json() == []
    assert AuditEntry.objects.filter(summary__contains="Eski Tedarikçi").count() == 1


def test_a_supplier_used_by_products_is_refused_with_the_count(owner_api):
    supplier = catalog.create_supplier("Mersin Toptan", "MT")
    category = catalog.create_category("Giyim")
    catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=category,
        shelf_price="100.00",
        supplier=supplier,
    )

    response = owner_api.delete(f"{ADMIN}/suppliers/{supplier.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "supplier_in_use"
    assert "1 ürün" in response.json()["message"]
    assert Supplier.objects.filter(pk=supplier.pk).exists()


def test_staff_cannot_delete_a_supplier(api_for):
    supplier = catalog.create_supplier("Mersin Toptan", "MT")

    response = api_for(staff_member("Staff")).delete(
        f"{ADMIN}/suppliers/{supplier.pk}",
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert Supplier.objects.filter(pk=supplier.pk).exists()


def a_product():
    return catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Giyim"),
        shelf_price="100.00",
    )


def test_the_owner_deletes_an_unused_colour_and_it_is_audited(owner_api):
    colour = catalog.create_colour("Eski Renk", "#112233")

    response = owner_api.delete(f"{ADMIN}/colours/{colour.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert owner_api.get(f"{ADMIN}/colours").json() == []
    assert AuditEntry.objects.filter(summary__contains="Eski Renk").count() == 1


def test_a_colour_used_by_a_variant_or_a_photo_is_refused_with_the_count(owner_api):
    colour = catalog.create_colour("Siyah", "#000000")
    product = a_product()
    catalog.create_variant(product, colour=colour, size="M", stock=1)
    ProductPhoto.objects.create(
        product=product,
        colour=colour,
        original="products/test.jpg",
        width=1000,
        height=1000,
        status=ProductPhoto.Status.READY,
    )

    response = owner_api.delete(f"{ADMIN}/colours/{colour.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "colour_in_use"
    assert "1 varyant" in response.json()["message"]
    assert "1 fotoğraf" in response.json()["message"]
    assert Colour.objects.filter(pk=colour.pk).exists()


def test_staff_cannot_delete_a_colour(api_for):
    colour = catalog.create_colour("Siyah", "#000000")

    response = api_for(staff_member("Staff")).delete(f"{ADMIN}/colours/{colour.pk}")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert Colour.objects.filter(pk=colour.pk).exists()


def order_for(variant, number="100001"):
    order = Order.objects.create(number=number, total_incl_tax=1, total_excl_tax=1)
    Line.objects.create(
        order=order,
        product=variant,
        title="x",
        partner_name="p",
        partner_sku="s",
        stockrecord=variant.stockrecords.get(),
        quantity=1,
        num_allocated=1,
        line_price_incl_tax=1,
        line_price_excl_tax=1,
        line_price_before_discounts_incl_tax=1,
        line_price_before_discounts_excl_tax=1,
        unit_price_incl_tax=1,
        unit_price_excl_tax=1,
    )


def product_with_variants(*sizes):
    product = a_product()
    colour = catalog.create_colour("Siyah", "#000000")
    variants = [
        catalog.create_variant(product, colour=colour, size=size, stock=1)
        for size in sizes
    ]
    return product, variants


def test_the_owner_deletes_a_variant_without_orders_and_it_is_audited(owner_api):
    _product, (small, medium) = product_with_variants("S", "M")

    response = owner_api.delete(f"{ADMIN}/variants/{small.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert not Product.objects.filter(pk=small.pk).exists()
    assert Product.objects.filter(pk=medium.pk).exists()
    assert AuditEntry.objects.filter(summary__contains="Varyant silindi").count() == 1


def test_a_variant_with_orders_is_refused_with_the_count(owner_api):
    _product, (small, _medium) = product_with_variants("S", "M")
    order_for(small)

    response = owner_api.delete(f"{ADMIN}/variants/{small.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "variant_has_orders"
    assert "1 sipariş" in response.json()["message"]
    assert Product.objects.filter(pk=small.pk).exists()


def test_the_last_variant_of_a_published_product_is_kept(owner_api):
    product, (only,) = product_with_variants("M")
    add_ready_photo(product)
    catalog.set_status(product, ProductStatus.PUBLISHED)

    response = owner_api.delete(f"{ADMIN}/variants/{only.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "last_variant"
    assert Product.objects.filter(pk=only.pk).exists()


def test_the_last_variant_of_a_draft_product_can_be_deleted(owner_api):
    _product, (only,) = product_with_variants("M")

    response = owner_api.delete(f"{ADMIN}/variants/{only.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT


def test_staff_cannot_delete_a_variant(api_for):
    _product, (small, _medium) = product_with_variants("S", "M")

    response = api_for(staff_member("Staff")).delete(f"{ADMIN}/variants/{small.pk}")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert Product.objects.filter(pk=small.pk).exists()


def test_the_owner_deletes_a_product_with_its_photos_and_redirects(owner_api):
    product, _variants = product_with_variants("S", "M")
    add_ready_photo(product)
    SlugRedirect.objects.create(old_slug="eski-elbise", product=product)

    response = owner_api.delete(f"{ADMIN}/products/{product.pk}")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert owner_api.get(f"{ADMIN}/products/{product.pk}").status_code == 404
    assert not Product.objects.filter(parent=product).exists()
    assert not SlugRedirect.objects.filter(old_slug="eski-elbise").exists()
    assert PendingFileRemoval.objects.filter(name="products/test.jpg").exists()
    assert AuditEntry.objects.filter(summary__contains="Ürün silindi").count() == 1


def test_a_product_with_orders_is_refused_with_the_count(owner_api):
    product, (small, medium) = product_with_variants("S", "M")
    order_for(small, "100001")
    order_for(medium, "100002")

    response = owner_api.delete(f"{ADMIN}/products/{product.pk}")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "product_has_orders"
    assert "2 sipariş" in response.json()["message"]
    assert Product.objects.filter(pk=product.pk).exists()
    assert not AuditEntry.objects.filter(summary__contains="silindi").exists()


def test_staff_cannot_delete_a_product(api_for):
    product, _variants = product_with_variants("M")

    response = api_for(staff_member("Staff")).delete(f"{ADMIN}/products/{product.pk}")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert Product.objects.filter(pk=product.pk).exists()
