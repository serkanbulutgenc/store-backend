# ruff: noqa: EM101
"""Catalog rules: categories, colours, Products, Variants, SKUs, slugs, status.

A Product is an Oscar *parent* Product with a `ProductProfile`; a Variant is an Oscar
*child* Product with a `VariantProfile` and one stock record. Everything here runs in
one transaction per use case and raises the API errors, so handlers only map them.
"""

from __future__ import annotations

import re
from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from django.utils.text import slugify
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductCategory
from oscar.apps.partner.models import Partner
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import SlugRedirect
from store_backend.catalog.models import Supplier
from store_backend.catalog.models import VariantProfile
from store_backend.catalog.signals import has_orders
from store_backend.catalog.sizes import sizes_for

if TYPE_CHECKING:
    from oscar.apps.catalogue.models import ProductClass

PARTNER_NAME = "Aura Tekstil"
SKU_PREFIX = "AUR"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# fmt: off
TURKISH = str.maketrans(
    {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "Ç": "c",
        "Ğ": "g",
        "I": "i",
        "İ": "i",
        "Ö": "o",
        "Ş": "s",
        "Ü": "u",
    },
)
# fmt: on
MAX_CATEGORY_DEPTH = 2


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def slugify_tr(text: str) -> str:
    """URL slug with Turkish letters transliterated (ç→c, ğ→g, ı/İ→i, ö→o, ş→s, ü→u)."""
    return slugify(text.translate(TURKISH))


def _money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise _invalid(field, "Geçerli bir tutar girin.") from None
    if amount < 0:
        raise _invalid(field, "Tutar negatif olamaz.")
    return amount


# --- slugs -----------------------------------------------------------------


def _slug_taken(slug: str, *, product: Product | None = None) -> bool:
    others = ProductProfile.objects.filter(slug=slug)
    redirects = SlugRedirect.objects.filter(old_slug=slug)
    if product is not None:
        others = others.exclude(product=product)
        redirects = redirects.exclude(product=product)
    return others.exists() or redirects.exists()


def _unique_slug(base: str, taken) -> str:
    base = base or "urun"
    slug, n = base, 1
    while taken(slug):
        n += 1
        slug = f"{base}-{n}"
    return slug


@transaction.atomic
def rename_slug(product: Product, new_slug: str) -> Product:
    """Change a Product's slug; the old one keeps working as a redirect."""
    slug = slugify_tr(new_slug)
    if not slug:
        raise _invalid("slug", "Geçerli bir adres girin.")
    profile = product.profile
    if slug == profile.slug:
        return product
    if _slug_taken(slug, product=product):
        msg = "Bu adres başka bir üründe kullanılıyor."
        raise ConflictError(msg, code="slug_taken", fields={"slug": [msg]})
    SlugRedirect.objects.filter(old_slug=slug, product=product).delete()
    SlugRedirect.objects.create(old_slug=profile.slug, product=product)
    profile.slug = slug
    profile.save(update_fields=["slug"])
    product.slug = slug
    product.save(update_fields=["slug"])
    return product


def resolve_slug(slug: str) -> tuple[Product, bool]:
    """The Product for a slug and whether the slug was an old one (redirect it)."""
    profile = ProductProfile.objects.select_related("product").filter(slug=slug).first()
    if profile:
        return profile.product, False
    redirect = (
        SlugRedirect.objects.select_related("product").filter(old_slug=slug).first()
    )
    if redirect:
        return redirect.product, True
    raise NotFoundError


# --- categories, colours ----------------------------------------------------


@transaction.atomic
def create_category(name: str, parent: Category | None = None) -> Category:
    """A node in the two-level tree: a top-level Category or one under it."""
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    if parent is not None and parent.depth >= MAX_CATEGORY_DEPTH:
        raise _invalid("parent", "Kategoriler en fazla iki seviye olabilir.")
    slug = _unique_slug(
        slugify_tr(name),
        lambda s: Category.objects.filter(slug=s).exists(),
    )
    if parent is None:
        return Category.objects.add_root({"name": name, "slug": slug})
    return Category.objects.add_child(parent, {"name": name, "slug": slug})


@transaction.atomic
def set_category(product: Product, category: Category | None) -> None:
    """Give the Product exactly one primary Category."""
    if category is None:
        raise _invalid("category", "Bir kategori seçin.")
    ProductCategory.objects.filter(product=product).exclude(category=category).delete()
    ProductCategory.objects.get_or_create(product=product, category=category)


def _colour_code(name: str) -> str:
    letters = re.sub(r"[^A-Z0-9]", "", slugify_tr(name).upper()) or "CLR"
    base = letters[:3].ljust(3, "X")
    if not Colour.objects.filter(code=base).exists():
        return base
    for n in range(2, 100):
        code = f"{base[:2]}{n % 10}" if n < 10 else f"{base[:1]}{n:02d}"  # noqa: PLR2004
        if not Colour.objects.filter(code=code).exists():
            return code
    raise ConflictError  # pragma: no cover


@transaction.atomic
def create_colour(name: str, hex_code: str, code: str | None = None) -> Colour:
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    if not HEX_RE.match(hex_code):
        raise _invalid("hex_code", "Renk kodu #RRGGBB biçiminde olmalı.")
    if Colour.objects.filter(name__iexact=name).exists():
        msg = "Bu renk zaten var."
        raise ConflictError(msg, code="colour_taken", fields={"name": [msg]})
    return Colour.objects.create(
        name=name,
        hex_code=hex_code.upper(),
        code=(code or _colour_code(name)).upper(),
    )


# --- products --------------------------------------------------------------


@transaction.atomic
def create_product(  # noqa: PLR0913
    *,
    title: str,
    product_class: ProductClass,
    category: Category | None,
    shelf_price: object,
    old_price: object | None = None,
    brand: Brand | None = None,
    supplier: Supplier | None = None,
    supplier_code: str = "",
    cost_price: object | None = None,
    kdv_rate: object | None = None,
    low_stock_threshold: int | None = None,
    slug: str | None = None,
) -> Product:
    title = title.strip()
    if not title:
        raise _invalid("title", "Ürün adı girin.")
    shelf = _money(shelf_price, "shelf_price")
    old = _money(old_price, "old_price") if old_price is not None else None
    if old is not None and old <= shelf:
        raise _invalid("old_price", "Eski fiyat satış fiyatından yüksek olmalı.")
    if slug:
        final_slug = slugify_tr(slug)
        if not final_slug or _slug_taken(final_slug):
            raise _invalid("slug", "Geçerli ve benzersiz bir adres girin.")
    else:
        final_slug = _unique_slug(slugify_tr(title), _slug_taken)

    product = Product.objects.create(
        structure=Product.PARENT,
        title=title,
        slug=final_slug,
        product_class=product_class,
        is_public=False,
    )
    extra = {}
    if low_stock_threshold is not None:
        extra["low_stock_threshold"] = low_stock_threshold
    ProductProfile.objects.create(
        product=product,
        slug=final_slug,
        brand=brand,
        supplier=supplier,
        supplier_code=supplier_code,
        cost_price=_money(cost_price, "cost_price") if cost_price is not None else None,
        shelf_price=shelf,
        old_price=old,
        kdv_rate=Decimal(str(kdv_rate)) if kdv_rate is not None else None,
        **extra,
    )
    set_category(product, category)
    return product


def kdv_rate_for(product: Product) -> Decimal:
    """The KDV percentage: the Product's own override or its class default."""
    parent = product.parent or product
    profile = parent.profile
    return profile.kdv_rate or parent.product_class.profile.default_kdv_rate


def shelf_price_for(variant: Product) -> Decimal:
    """KDV-inclusive price: the Variant's override, else the Product's shelf price."""
    override = variant.variant_profile.price_override
    return override if override is not None else variant.parent.profile.shelf_price


@transaction.atomic
def set_status(product: Product, status: ProductStatus) -> Product:
    if status == ProductStatus.PUBLISHED and not product.children.exists():
        raise _invalid("status", "Yayınlamak için en az bir varyant ekleyin.")
    profile = product.profile
    profile.status = status
    profile.save(update_fields=["status"])
    public = status == ProductStatus.PUBLISHED
    Product.objects.filter(pk=product.pk).update(is_public=public)
    product.children.update(is_public=public)
    product.is_public = public
    return product


def archive_product(product: Product) -> Product:
    return set_status(product, ProductStatus.ARCHIVED)


@transaction.atomic
def delete_product(product: Product) -> None:
    """Hard delete; refused for a Product with Orders (archive it instead)."""
    if has_orders(product):
        msg = "Siparişi olan ürün silinemez; arşivleyin."
        raise ConflictError(msg, code="product_has_orders")
    product.delete()


# --- variants --------------------------------------------------------------


def _size_token(size: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", slugify_tr(size).upper())


def generate_sku(product: Product, colour: Colour, size: str) -> str:
    """`AUR-000123-KRM-38`; a numeric suffix is added if that code is taken."""
    base = f"{SKU_PREFIX}-{product.pk:06d}-{colour.code}-{_size_token(size)}"
    return _unique_slug_like(base)


def _unique_slug_like(base: str) -> str:
    sku, n = base, 1
    while VariantProfile.objects.filter(sku=sku).exists():
        n += 1
        sku = f"{base}-{n}"
    return sku


def _conflict(field: str, message: str, code: str) -> ConflictError:
    return ConflictError(message, code=code, fields={field: [message]})


@transaction.atomic
def create_variant(  # noqa: PLR0913
    product: Product,
    *,
    colour: Colour,
    size: str,
    sku: str | None = None,
    barcode: str | None = None,
    stock: int = 0,
    price_override: object | None = None,
) -> Product:
    scheme = product.product_class.profile.size_scheme
    if size not in sizes_for(scheme):
        raise _invalid("size", "Bu ürün sınıfı için geçersiz beden.")
    if stock < 0:
        raise _invalid("stock", "Stok negatif olamaz.")
    if VariantProfile.objects.filter(parent=product, colour=colour, size=size).exists():
        raise _conflict("size", "Bu renk ve beden zaten var.", "variant_exists")
    sku = sku.strip() if sku else None
    if sku:
        if VariantProfile.objects.filter(sku=sku).exists():
            raise _conflict("sku", "Bu SKU zaten kullanılıyor.", "sku_taken")
    else:
        sku = generate_sku(product, colour, size)
    barcode = barcode.strip() or None if barcode else None
    if barcode and VariantProfile.objects.filter(barcode=barcode).exists():
        raise _conflict("barcode", "Bu barkod zaten kullanılıyor.", "barcode_taken")
    override = (
        _money(price_override, "price_override") if price_override is not None else None
    )

    try:
        with transaction.atomic():
            variant = Product.objects.create(
                structure=Product.CHILD,
                parent=product,
                upc=sku,
                is_public=product.is_public,
            )
            VariantProfile.objects.create(
                product=variant,
                parent=product,
                colour=colour,
                size=size,
                sku=sku,
                barcode=barcode,
                price_override=override,
            )
    except IntegrityError:
        msg = "Varyant kaydedilemedi: çakışan değer."
        raise _conflict("sku", msg, "variant_conflict") from None
    partner, _ = Partner.objects.get_or_create(name=PARTNER_NAME)
    StockRecord.objects.create(
        product=variant,
        partner=partner,
        partner_sku=sku,
        num_in_stock=stock,
        price_currency="TRY",
    )
    return variant


def is_low_stock(variant: Product) -> bool:
    """At or below the Product's low-stock threshold (counting reserved units)."""
    record = variant.stockrecords.get()
    threshold = variant.parent.profile.low_stock_threshold
    return record.net_stock_level <= threshold
