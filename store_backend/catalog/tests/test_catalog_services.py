from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction
from django.db.models import ProtectedError
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.order.models import Line
from oscar.apps.order.models import Order

from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import SlugRedirect
from store_backend.catalog.tests.conftest import add_ready_photo
from store_backend.services import catalog
from store_backend.services import stock

pytestmark = pytest.mark.django_db

ACCESSORY_KDV = 20
STOCK = 5
SECOND = 2


@pytest.fixture
def giyim():
    return ProductClass.objects.get(name="Giyim")


@pytest.fixture
def elbise():
    top = catalog.create_category("Giyim")
    return catalog.create_category("Elbise", parent=top)


@pytest.fixture
def kirmizi():
    return catalog.create_colour("Kırmızı", "#C0392B")


@pytest.fixture
def product(giyim, elbise):
    return catalog.create_product(
        title="Çiçekli Şık Elbise",
        product_class=giyim,
        category=elbise,
        shelf_price=Decimal("349.90"),
    )


def test_seeded_product_classes_fix_size_scheme_kdv_and_hygiene():
    giyim = ProductClass.objects.get(name="Giyim").profile
    assert (giyim.size_scheme, giyim.default_kdv_rate, giyim.is_hygiene) == (
        "letters",
        Decimal("10.00"),
        False,
    )
    assert ProductClass.objects.get(name="İç Giyim").profile.is_hygiene
    assert (
        ProductClass.objects.get(name="Aksesuar").profile.default_kdv_rate
        == ACCESSORY_KDV
    )


# --- slugs -----------------------------------------------------------------


def test_slug_is_transliterated_from_turkish(product):
    assert product.slug == "cicekli-sik-elbise"
    assert product.profile.slug == "cicekli-sik-elbise"


def test_dotted_and_dotless_i_transliterate():
    assert catalog.slugify_tr("IŞIK İPEK ığdır") == "isik-ipek-igdir"


def test_slugs_are_unique(giyim, elbise):
    a = catalog.create_product(
        title="Elbise",
        product_class=giyim,
        category=elbise,
        shelf_price=100,
    )
    b = catalog.create_product(
        title="Elbise",
        product_class=giyim,
        category=elbise,
        shelf_price=100,
    )
    assert a.slug == "elbise"
    assert b.slug == "elbise-2"


def test_renaming_a_slug_records_a_redirect(product):
    catalog.rename_slug(product, "Yazlık Elbise")
    product.refresh_from_db()
    assert product.slug == "yazlik-elbise"
    assert SlugRedirect.objects.get(old_slug="cicekli-sik-elbise").product == product
    assert catalog.resolve_slug("cicekli-sik-elbise") == (product, True)
    assert catalog.resolve_slug("yazlik-elbise") == (product, False)


def test_rename_to_a_taken_slug_conflicts(product, giyim, elbise):
    other = catalog.create_product(
        title="Başka",
        product_class=giyim,
        category=elbise,
        shelf_price=100,
    )
    with pytest.raises(ConflictError):
        catalog.rename_slug(other, product.slug)


def test_a_slug_that_is_someone_elses_redirect_is_taken(product, giyim, elbise):
    catalog.rename_slug(product, "yeni")
    other = catalog.create_product(
        title="Başka",
        product_class=giyim,
        category=elbise,
        shelf_price=100,
    )
    with pytest.raises(ConflictError):
        catalog.rename_slug(other, "cicekli-sik-elbise")


def test_renaming_back_to_an_old_slug_drops_its_redirect(product):
    catalog.rename_slug(product, "yeni")
    catalog.rename_slug(product, "cicekli-sik-elbise")
    assert not SlugRedirect.objects.filter(old_slug="cicekli-sik-elbise").exists()
    assert SlugRedirect.objects.filter(old_slug="yeni").exists()


# --- categories ------------------------------------------------------------


def test_categories_are_two_levels(elbise):
    with pytest.raises(UnprocessableError):
        catalog.create_category("Mini", parent=elbise)


def test_product_has_exactly_one_primary_category(product, elbise):
    other = catalog.create_category(
        "Ayakkabı",
        parent=Category.objects.get_parent(elbise),
    )
    catalog.set_category(product, other)
    assert list(product.categories.all()) == [other]


def test_product_requires_a_category(giyim):
    with pytest.raises(UnprocessableError):
        catalog.create_product(
            title="X",
            product_class=giyim,
            category=None,
            shelf_price=1,
        )


# --- variants --------------------------------------------------------------


def test_variant_gets_auto_sku_and_stock(product, kirmizi):
    variant = catalog.create_variant(product, colour=kirmizi, size="M", stock=5)
    assert variant.variant_profile.sku == f"AUR-{product.pk:06d}-KIR-M"
    assert variant.parent == product
    assert variant.stockrecords.get().num_in_stock == STOCK
    assert variant.stockrecords.get().partner_sku == variant.variant_profile.sku


def test_sku_can_be_edited_at_creation_and_must_be_unique(product, kirmizi):
    catalog.create_variant(product, colour=kirmizi, size="M", sku="CUSTOM-1")
    with pytest.raises(ConflictError):
        catalog.create_variant(product, colour=kirmizi, size="L", sku="CUSTOM-1")


def test_auto_sku_collision_gets_a_suffix(product, kirmizi):
    catalog.create_variant(
        product,
        colour=kirmizi,
        size="M",
        sku=f"AUR-{product.pk:06d}-KIR-L",
    )
    v = catalog.create_variant(product, colour=kirmizi, size="L")
    assert v.variant_profile.sku.endswith("-KIR-L-2")


def test_size_must_come_from_the_class_list(product, kirmizi):
    with pytest.raises(UnprocessableError) as exc:
        catalog.create_variant(product, colour=kirmizi, size="38")
    assert "size" in exc.value.fields


def test_shoe_sizes_are_numbers(kirmizi):
    shoes = ProductClass.objects.get(name="Ayakkabı")
    cat = catalog.create_category("Ayakkabı")
    p = catalog.create_product(
        title="Sandalet",
        product_class=shoes,
        category=cat,
        shelf_price=500,
    )
    assert (
        catalog.create_variant(p, colour=kirmizi, size="38").variant_profile.size
        == "38"
    )
    with pytest.raises(UnprocessableError):
        catalog.create_variant(p, colour=kirmizi, size="M")


def test_colour_size_pair_is_unique_per_product(product, kirmizi):
    catalog.create_variant(product, colour=kirmizi, size="M")
    with pytest.raises(ConflictError):
        catalog.create_variant(product, colour=kirmizi, size="M")


def test_barcode_is_optional_but_unique(product, kirmizi):
    catalog.create_variant(product, colour=kirmizi, size="S")
    catalog.create_variant(product, colour=kirmizi, size="M", barcode="869000000001")
    with pytest.raises(ConflictError):
        catalog.create_variant(
            product,
            colour=kirmizi,
            size="L",
            barcode="869000000001",
        )


def test_colour_code_is_generated_and_unique():
    a = catalog.create_colour("Kırmızı", "#C0392B")
    b = catalog.create_colour("Kırmızı Bordo", "#800000")
    assert a.code == "KIR"
    assert b.code != a.code


def test_colour_hex_is_validated():
    with pytest.raises(UnprocessableError):
        catalog.create_colour("Mavi", "blue")


# --- prices and thresholds --------------------------------------------------


def test_price_defaults_and_variant_override(product, kirmizi):
    v = catalog.create_variant(
        product,
        colour=kirmizi,
        size="M",
        price_override=Decimal("299.90"),
    )
    assert catalog.shelf_price_for(v) == Decimal("299.90")
    other = catalog.create_variant(product, colour=kirmizi, size="L")
    assert catalog.shelf_price_for(other) == Decimal("349.90")


def test_old_price_must_exceed_shelf_price(giyim, elbise):
    with pytest.raises(UnprocessableError):
        catalog.create_product(
            title="X",
            product_class=giyim,
            category=elbise,
            shelf_price=100,
            old_price=90,
        )


def test_low_stock_threshold_default_and_override(product, kirmizi):
    v = catalog.create_variant(product, colour=kirmizi, size="M", stock=2)
    assert product.profile.low_stock_threshold == SECOND
    assert stock.is_low(v.stockrecords.get(), product.profile.low_stock_threshold)
    product.profile.low_stock_threshold = 0
    product.profile.save()
    assert not stock.is_low(v.stockrecords.get(), 0)


def test_kdv_rate_comes_from_class_unless_overridden(product):
    assert catalog.kdv_rate_for(product) == Decimal("10.00")
    product.profile.kdv_rate = Decimal("20.00")
    assert catalog.kdv_rate_for(product) == Decimal("20.00")


# --- status, archive, delete -------------------------------------------------


def test_publish_mirrors_to_oscar_visibility(product, kirmizi):
    v = catalog.create_variant(product, colour=kirmizi, size="M")
    add_ready_photo(product)
    catalog.set_status(product, ProductStatus.PUBLISHED)
    product.refresh_from_db()
    v.refresh_from_db()
    assert product.is_public
    assert v.is_public
    catalog.set_status(product, ProductStatus.ARCHIVED)
    product.refresh_from_db()
    assert not product.is_public


def test_publishing_needs_a_variant(product):
    with pytest.raises(UnprocessableError):
        catalog.set_status(product, ProductStatus.PUBLISHED)


def _order_for(variant):
    order = Order.objects.create(number="100001", total_incl_tax=1, total_excl_tax=1)
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


def test_product_without_orders_can_be_deleted(product, kirmizi):
    catalog.create_variant(product, colour=kirmizi, size="M")
    catalog.delete_product(product)
    assert not Product.objects.filter(pk=product.pk).exists()


def test_product_with_orders_cannot_be_deleted(product, kirmizi):
    v = catalog.create_variant(product, colour=kirmizi, size="M", stock=3)
    _order_for(v)
    with pytest.raises(ConflictError):
        catalog.delete_product(product)
    assert Product.objects.filter(pk=product.pk).exists()


def test_orm_hard_delete_with_orders_is_also_rejected(product, kirmizi):
    v = catalog.create_variant(product, colour=kirmizi, size="M", stock=3)
    _order_for(v)
    # pre_delete runs inside Django's non-savepoint delete block, so isolate it.
    with pytest.raises(ProtectedError), transaction.atomic():
        product.delete()
    assert Product.objects.filter(pk=product.pk).exists()


def test_explicit_zero_kdv_override_is_respected(product):
    product.profile.kdv_rate = Decimal("0.00")
    assert catalog.kdv_rate_for(product) == Decimal(0)


def test_kdv_rate_and_threshold_are_validated(giyim, elbise):
    with pytest.raises(UnprocessableError):
        catalog.create_product(
            title="X",
            product_class=giyim,
            category=elbise,
            shelf_price=1,
            kdv_rate=150,
        )
    with pytest.raises(UnprocessableError):
        catalog.create_product(
            title="X",
            product_class=giyim,
            category=elbise,
            shelf_price=1,
            low_stock_threshold=-1,
        )
