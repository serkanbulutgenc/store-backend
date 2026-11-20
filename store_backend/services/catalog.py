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
from django.db.models import Count
from django.db.models import OuterRef
from django.db.models import Subquery
from django.db.models.functions import Coalesce
from django.utils.text import slugify
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductCategory
from oscar.apps.order.models import Line
from oscar.apps.partner.models import Partner
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import SlugRedirect
from store_backend.catalog.models import Supplier
from store_backend.catalog.models import VariantProfile
from store_backend.catalog.signals import has_orders
from store_backend.catalog.sizes import sizes_for
from store_backend.services import audit
from store_backend.services import catalog_policy
from store_backend.services import shipping_admin
from store_backend.services import shop_settings
from store_backend.services import size_guides
from store_backend.shop.models import MenuItem

if TYPE_CHECKING:
    from collections.abc import Callable

    from oscar.apps.catalogue.models import ProductClass

    from store_backend.users.models import User

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
MAX_CATEGORY_DEPTH = 5
MAX_KDV_RATE = Decimal(100)


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
    """A node in the category tree: a top-level Category or one under another, up to
    `MAX_CATEGORY_DEPTH` levels."""
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    if parent is not None and parent.depth >= MAX_CATEGORY_DEPTH:
        raise _invalid("parent", "Kategoriler en fazla beş seviye olabilir.")
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


DESCRIPTION_MAX = 5000


def clean_description(value: object) -> str:
    """A Product Description: Markdown text kept as written, within the length cap."""
    text = str(value or "")
    if len(text) > DESCRIPTION_MAX:
        msg = f"Açıklama en fazla {DESCRIPTION_MAX} karakter olabilir."
        raise _invalid("description", msg)
    return text


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
    desi: int | None = None,
    slug: str | None = None,
    description: str = "",
    fit_note: str = "",
) -> Product:
    title = title.strip()
    if not title:
        raise _invalid("title", "Ürün adı girin.")
    shelf = _money(shelf_price, "shelf_price")
    old = _money(old_price, "old_price") if old_price is not None else None
    if old is not None and old <= shelf:
        raise _invalid("old_price", "Eski fiyat satış fiyatından yüksek olmalı.")
    rate = Decimal(str(kdv_rate)) if kdv_rate is not None else None
    if rate is not None and not Decimal(0) <= rate <= MAX_KDV_RATE:
        raise _invalid("kdv_rate", "KDV oranı 0 ile 100 arasında olmalı.")
    if low_stock_threshold is not None and low_stock_threshold < 0:
        raise _invalid("low_stock_threshold", "Eşik negatif olamaz.")
    if desi is not None:
        shipping_admin.validate_desi(desi)
    note = size_guides.clean_fit_note(fit_note)
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
        description=clean_description(description),
        is_public=False,
    )
    # An unset threshold takes the Store settings default.
    extra = {
        "low_stock_threshold": (
            low_stock_threshold
            if low_stock_threshold is not None
            else shop_settings.get().low_stock_default
        ),
    }
    ProductProfile.objects.create(
        product=product,
        slug=final_slug,
        brand=brand,
        supplier=supplier,
        supplier_code=supplier_code,
        cost_price=_money(cost_price, "cost_price") if cost_price is not None else None,
        shelf_price=shelf,
        old_price=old,
        kdv_rate=rate,
        desi=desi,
        fit_note=note,
        **extra,
    )
    set_category(product, category)
    return product


def kdv_rate_for(product: Product) -> Decimal:
    """The KDV percentage: the Product's own override or its class default."""
    parent = product.parent or product
    profile = parent.profile
    if profile.kdv_rate is not None:
        return profile.kdv_rate
    return parent.product_class.profile.default_kdv_rate


def desi_for(product: Product) -> int:
    """Carrier size of one item: the Product's own desi or its class default."""
    parent = product.parent or product
    override = parent.profile.desi
    return (
        override if override is not None else parent.product_class.profile.default_desi
    )


def shelf_price_for(variant: Product) -> Decimal:
    """KDV-inclusive price: the Variant's override, else the Product's shelf price."""
    override = variant.variant_profile.price_override
    return override if override is not None else variant.parent.profile.shelf_price


# Extra publish rules registered by other modules (the media ticket adds "needs a
# photo"). Each is called with the Product and raises an ApiError to refuse.
PUBLISH_CHECKS: list[Callable[[Product], None]] = []


@transaction.atomic
def set_status(product: Product, status: ProductStatus) -> Product:
    if status == ProductStatus.PUBLISHED:
        # Serialises with photo deletes, which take the same lock.
        Product.objects.select_for_update().get(pk=product.pk)
        if not product.children.exists():
            raise _invalid("status", "Yayınlamak için en az bir varyant ekleyin.")
        for check in PUBLISH_CHECKS:
            check(product)
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
    """Hard delete; refused for a Product with Orders or Menu items (archive it
    instead)."""
    if has_orders(product):
        msg = "Siparişi olan ürün silinemez; arşivleyin."
        raise ConflictError(msg, code="product_has_orders")
    if in_menus := product_menu_items(product):
        msg = (
            f"Bu ürüne {in_menus} menü öğesi bağlı; önce menüden kaldırın. "
            "Arşivlemek de bir seçenek."
        )
        raise ConflictError(msg, code="product_in_menu")
    product.delete()


def order_line_count(product: Product) -> int:
    """Order lines that reference this Product or any of its Variants."""
    ids = [product.pk, *product.children.values_list("pk", flat=True)]
    return Line.objects.filter(product_id__in=ids).count()


def product_menu_items(product: Product) -> int:
    """How many Menu items point at this Product (ADR 0007)."""
    return MenuItem.objects.filter(product=product).count()


def with_menu_item_count(products):
    """Annotate Products with `menu_item_count`: Menu items pointing at each (ADR 0007).

    A correlated subquery, so a list costs no extra query and a join in the caller's
    filter cannot multiply the count.
    """
    pointing = (
        MenuItem.objects.filter(product=OuterRef("pk"))
        .order_by()
        .values("product")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return products.annotate(menu_item_count=Coalesce(Subquery(pointing), 0))


@transaction.atomic
def remove_product(actor: User, product: Product) -> None:
    """Owner delete of a Product with no Orders (ADR 0005). Audited.

    Photos, Variants and slug redirects go with it; the photo files are queued for
    removal by the photo delete signal.
    """
    product = Product.objects.select_for_update().get(pk=product.pk)
    if lines := order_line_count(product):
        msg = f"Bu ürün {lines} siparişte var; silinemez. Arşivleyebilirsiniz."
        raise ConflictError(msg, code="product_has_orders")
    audit.record(
        actor,
        audit.Action.PRODUCT_DELETED,
        target=product,
        summary=f"Ürün silindi: {product.title}",
        before={"title": product.title, "slug": product.profile.slug},
    )
    delete_product(product)


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
        num_allocated=0,
        price_currency="TRY",
    )
    return variant


@transaction.atomic
def generate_variants(
    product: Product,
    *,
    colour_ids: list[int],
    sizes: list[str],
    stock: int = 0,
    price_override: object | None = None,
) -> tuple[list[Product], list[tuple[Colour, str]]]:
    """Create every missing colour x size Variant of `product` in one transaction.

    Returns (created, skipped); existing combinations are skipped, not errors. The
    whole request is refused if any colour is unknown or inactive or any size is
    outside the class's scheme, before anything is created.
    """
    colour_ids = list(dict.fromkeys(colour_ids))
    sizes = list(dict.fromkeys(sizes))
    if not colour_ids:
        raise _invalid("colour_ids", "En az bir renk seçin.")
    if not sizes:
        raise _invalid("sizes", "En az bir beden seçin.")
    colours = {c.pk: c for c in Colour.objects.filter(pk__in=colour_ids)}
    if len(colours) != len(colour_ids):
        raise _invalid("colour_ids", "Kayıt bulunamadı.")
    if any(not c.is_active for c in colours.values()):
        raise _invalid("colour_ids", "Pasif renk seçilemez.")
    allowed = sizes_for(product.product_class.profile.size_scheme)
    if any(size not in allowed for size in sizes):
        raise _invalid("sizes", "Bu ürün sınıfı için geçersiz beden.")
    existing = set(
        VariantProfile.objects.filter(parent=product).values_list("colour_id", "size"),
    )
    created: list[Product] = []
    skipped: list[tuple[Colour, str]] = []
    for colour_id in colour_ids:
        for size in sizes:
            colour = colours[colour_id]
            if (colour_id, size) in existing:
                skipped.append((colour, size))
                continue
            created.append(
                create_variant(
                    product,
                    colour=colour,
                    size=size,
                    stock=stock,
                    price_override=price_override,
                ),
            )
    return created, skipped


# --- updates ---------------------------------------------------------------

PRODUCT_FIELDS = frozenset(
    {
        "title",
        "category",
        "brand",
        "supplier",
        "supplier_code",
        "cost_price",
        "shelf_price",
        "old_price",
        "kdv_rate",
        "low_stock_threshold",
        "desi",
        "slug",
        "description",
        "size_guide",
        "fit_note",
    },
)
PRICE_FIELDS = ("shelf_price", "old_price", "cost_price")


def _price_snapshot(profile: ProductProfile) -> dict[str, str | None]:
    return {
        f: None if (v := getattr(profile, f)) is None else str(v) for f in PRICE_FIELDS
    }


@transaction.atomic
def update_product(  # noqa: C901, PLR0912, PLR0915
    actor: User,
    product: Product,
    **changes: object,
) -> Product:
    """Apply the given changes (only the keys passed). Price changes are audited.

    Takes resolved objects; the Dashboard API goes through `patch_product`, which
    resolves ids and enforces the Owner-only field policy first.
    """
    unknown = set(changes) - PRODUCT_FIELDS
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    profile = ProductProfile.objects.select_for_update().get(product=product)
    before = _price_snapshot(profile)
    guide_before, note_before = profile.size_guide, profile.fit_note

    if "title" in changes:
        title = str(changes["title"]).strip()
        if not title:
            raise _invalid("title", "Ürün adı girin.")
        product.title = title
        product.save(update_fields=["title"])
    if "description" in changes:
        product.description = clean_description(changes["description"])
        product.save(update_fields=["description"])
    if "category" in changes:
        set_category(product, changes["category"])
    for name in ("brand", "supplier", "supplier_code"):
        if name in changes:
            setattr(
                profile,
                name,
                changes[name] if name != "supplier_code" else str(changes[name] or ""),
            )
    if "cost_price" in changes:
        value = changes["cost_price"]
        profile.cost_price = None if value is None else _money(value, "cost_price")
    if "shelf_price" in changes:
        profile.shelf_price = _money(changes["shelf_price"], "shelf_price")
    if "old_price" in changes:
        value = changes["old_price"]
        profile.old_price = None if value is None else _money(value, "old_price")
    if profile.old_price is not None and profile.old_price <= profile.shelf_price:
        raise _invalid("old_price", "Eski fiyat satış fiyatından yüksek olmalı.")
    if "kdv_rate" in changes:
        value = changes["kdv_rate"]
        rate = None if value is None else Decimal(str(value))
        if rate is not None and not Decimal(0) <= rate <= MAX_KDV_RATE:
            raise _invalid("kdv_rate", "KDV oranı 0 ile 100 arasında olmalı.")
        profile.kdv_rate = rate
    if "low_stock_threshold" in changes:
        threshold = changes["low_stock_threshold"]
        if not isinstance(threshold, int) or threshold < 0:
            raise _invalid("low_stock_threshold", "Eşik negatif olamaz.")
        profile.low_stock_threshold = threshold
    if "desi" in changes:
        value = changes["desi"]
        profile.desi = None if value is None else shipping_admin.validate_desi(value)
    if "size_guide" in changes:
        profile.size_guide = size_guides.clean(changes["size_guide"], "size_guide")
    if "fit_note" in changes:
        profile.fit_note = size_guides.clean_fit_note(changes["fit_note"])
    profile.save()
    if "slug" in changes:
        rename_slug(product, str(changes["slug"]))

    size_guides.audit_product_edits(
        actor,
        product,
        guide=(guide_before, profile.size_guide),
        fit_note=(note_before, profile.fit_note),
    )
    after = _price_snapshot(profile)
    if after != before:
        audit.record(
            actor,
            audit.Action.PRICE_CHANGE,
            target=product,
            summary=product.title,
            before=before,
            after=after,
        )
    return product


@transaction.atomic
@transaction.atomic
def delete_variant(actor: User, variant: Product) -> None:
    """Delete a Variant with no Orders, never the last of a published Product."""
    parent = Product.objects.select_for_update().get(pk=variant.parent_id)
    if lines := order_line_count(variant):
        msg = f"Bu varyant {lines} siparişte var; silinemez."
        raise ConflictError(msg, code="variant_has_orders")
    published = parent.profile.status == ProductStatus.PUBLISHED
    if published and parent.children.count() == 1:
        msg = "Yayındaki ürünün son varyantı silinemez; önce ürünü yayından kaldırın."
        raise ConflictError(msg, code="last_variant")
    audit.record(
        actor,
        audit.Action.VARIANT_DELETED,
        target=parent,
        summary=f"Varyant silindi: {variant.variant_profile.sku}",
        before={"sku": variant.variant_profile.sku, "product": parent.title},
    )
    variant.delete()


def update_variant(actor: User, variant: Product, **changes: object) -> Product:
    """Edit a Variant's SKU, barcode or price override (colour and size are fixed)."""
    unknown = set(changes) - {"sku", "barcode", "price_override"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    vp = variant.variant_profile
    before = {
        "price_override": None if vp.price_override is None else str(vp.price_override),
    }
    if "sku" in changes:
        sku = str(changes["sku"] or "").strip()
        if not sku:
            raise _invalid("sku", "SKU girin.")
        if VariantProfile.objects.filter(sku=sku).exclude(pk=vp.pk).exists():
            raise _conflict("sku", "Bu SKU zaten kullanılıyor.", "sku_taken")
        vp.sku = sku
        variant.upc = sku
        variant.save(update_fields=["upc"])
        variant.stockrecords.update(partner_sku=sku)
    if "barcode" in changes:
        barcode = str(changes["barcode"] or "").strip() or None
        if (
            barcode
            and VariantProfile.objects.filter(barcode=barcode)
            .exclude(pk=vp.pk)
            .exists()
        ):
            raise _conflict("barcode", "Bu barkod zaten kullanılıyor.", "barcode_taken")
        vp.barcode = barcode
    if "price_override" in changes:
        value = changes["price_override"]
        vp.price_override = None if value is None else _money(value, "price_override")
    vp.save()
    after = {
        "price_override": None if vp.price_override is None else str(vp.price_override),
    }
    if after != before:
        audit.record(
            actor, audit.Action.PRICE_CHANGE, target=variant, summary=vp.sku,
            before=before, after=after,
        )  # fmt: skip
    return variant


# --- lookups and use cases for the Dashboard API ------------------------------


def get_product(pk: int) -> Product:
    """A parent Product with its profiles loaded, or NotFoundError."""
    product = (
        Product.objects.select_related("profile", "product_class__profile")
        .filter(pk=pk, structure=Product.PARENT)
        .first()
    )
    if product is None:
        raise NotFoundError
    return product


def get_variant(pk: int) -> Product:
    """A child Product (Variant) with profile and colour loaded, or NotFoundError."""
    variant = (
        Product.objects.select_related("variant_profile__colour", "parent__profile")
        .filter(pk=pk, structure=Product.CHILD)
        .first()
    )
    if variant is None:
        raise NotFoundError
    return variant


def ref(model: type, pk: int | None, field: str):
    """The row a request refers to by id (None stays None), else a 422 on `field`."""
    if pk is None:
        return None
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        msg = "Kayıt bulunamadı."
        raise _invalid(field, msg)
    return obj


def patch_product(actor: User, pk: int, patch: dict[str, object]) -> Product:
    """Update a Product from an API patch: `*_id` keys are resolved to objects and
    renamed; returns the Product reloaded with its new state.
    """
    catalog_policy.require_owner_fields_allowed(actor, patch)
    product = get_product(pk)
    changes = dict(patch)
    if "category_id" in changes:
        changes["category"] = ref(Category, changes.pop("category_id"), "category_id")
    if "brand_id" in changes:
        changes["brand"] = ref(Brand, changes.pop("brand_id"), "brand_id")
    if "supplier_id" in changes:
        changes["supplier"] = ref(Supplier, changes.pop("supplier_id"), "supplier_id")
    update_product(actor, product, **changes)
    return get_product(pk)


def change_status(actor: User, pk: int, status: ProductStatus) -> Product:
    """Publish, return to draft or archive; archiving is Owner-only."""
    product = get_product(pk)
    catalog_policy.require_status_change_allowed(actor, product, status)
    set_status(product, status)
    return get_product(pk)


# --- brands, suppliers, colours, categories -----------------------------------


def _named(model, name: str, **extra):
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    if model.objects.filter(name__iexact=name).exists():
        raise _conflict("name", "Bu ad zaten var.", "name_taken")
    return model.objects.create(name=name, **extra)


def create_brand(name: str) -> Brand:
    return _named(Brand, name)


def create_supplier(name: str, code: str) -> Supplier:
    code = code.strip()
    if not code:
        raise _invalid("code", "Tedarikçi kodu girin.")
    if Supplier.objects.filter(code__iexact=code).exists():
        raise _conflict("code", "Bu kod zaten var.", "code_taken")
    return _named(Supplier, name, code=code)


def _rename(obj, name: str | None) -> None:
    if name is None:
        return
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    if type(obj).objects.filter(name__iexact=name).exclude(pk=obj.pk).exists():
        raise _conflict("name", "Bu ad zaten var.", "name_taken")
    obj.name = name


def brand_menu_items(brand: Brand) -> int:
    """How many Menu items point at this Brand (ADR 0007)."""
    return MenuItem.objects.filter(brand=brand).count()


def brand_menu_item_counts() -> dict[int, int]:
    """Menu items pointing at each Brand, by Brand id (one query for a list)."""
    return dict(
        MenuItem.objects.filter(brand__isnull=False)
        .values_list("brand_id")
        .annotate(n=Count("pk"))
        .values_list("brand_id", "n"),
    )


@transaction.atomic
def brand_blockers(brand: Brand) -> int:
    """How many Products use this Brand (what blocks deleting it)."""
    return ProductProfile.objects.filter(brand=brand).count()


@transaction.atomic
def delete_brand(actor: User, brand: Brand) -> None:
    """Delete an unused Brand (ADR 0005). Audited."""
    brand = Brand.objects.select_for_update().get(pk=brand.pk)
    if products := brand_blockers(brand):
        msg = f"Bu markayı {products} ürün kullanıyor; silinemez."
        raise ConflictError(msg, code="brand_in_use")
    if in_menus := brand_menu_items(brand):
        msg = f"Bu markaya {in_menus} menü öğesi bağlı; önce menüden kaldırın."
        raise ConflictError(msg, code="brand_in_menu")
    audit.record(
        actor,
        audit.Action.BRAND_DELETED,
        target=brand,
        summary=f"Marka silindi: {brand.name}",
        before={"name": brand.name},
    )
    from store_backend.services import media  # noqa: PLC0415 - media imports this module

    media._schedule_names(brand.logo_file_names())  # noqa: SLF001
    brand.delete()


def update_brand(brand: Brand, *, name: str | None = None) -> Brand:
    _rename(brand, name)
    brand.save()
    return brand


def supplier_blockers(supplier: Supplier) -> int:
    """How many Products use this Supplier (what blocks deleting it)."""
    return ProductProfile.objects.filter(supplier=supplier).count()


@transaction.atomic
def delete_supplier(actor: User, supplier: Supplier) -> None:
    """Delete an unused Supplier (ADR 0005). Audited."""
    supplier = Supplier.objects.select_for_update().get(pk=supplier.pk)
    if products := supplier_blockers(supplier):
        msg = f"Bu tedarikçiyi {products} ürün kullanıyor; silinemez."
        raise ConflictError(msg, code="supplier_in_use")
    audit.record(
        actor,
        audit.Action.SUPPLIER_DELETED,
        target=supplier,
        summary=f"Tedarikçi silindi: {supplier.name}",
        before={"name": supplier.name, "code": supplier.code},
    )
    supplier.delete()


@transaction.atomic
def update_supplier(
    supplier: Supplier,
    *,
    name: str | None = None,
    code: str | None = None,
) -> Supplier:
    _rename(supplier, name)
    if code is not None:
        code = code.strip()
        if not code:
            raise _invalid("code", "Tedarikçi kodu girin.")
        if Supplier.objects.filter(code__iexact=code).exclude(pk=supplier.pk).exists():
            raise _conflict("code", "Bu kod zaten var.", "code_taken")
        supplier.code = code
    supplier.save()
    return supplier


def colour_blockers(colour: Colour) -> dict[str, int]:
    """Variants and photos that use this Colour (what blocks deleting it)."""
    return {
        "variants": VariantProfile.objects.filter(colour=colour).count(),
        "photos": ProductPhoto.objects.filter(colour=colour).count(),
    }


@transaction.atomic
def delete_colour(actor: User, colour: Colour) -> None:
    """Delete an unused Colour (ADR 0005); an in-use one is deactivated instead."""
    colour = Colour.objects.select_for_update().get(pk=colour.pk)
    blockers = colour_blockers(colour)
    if any(blockers.values()):
        parts = []
        if blockers["variants"]:
            parts.append(f"{blockers['variants']} varyant")
        if blockers["photos"]:
            parts.append(f"{blockers['photos']} fotoğraf")
        msg = (
            f"Bu rengi {' ve '.join(parts)} kullanıyor; silinemez. "
            "Pasif yapabilirsiniz."
        )
        raise ConflictError(msg, code="colour_in_use")
    audit.record(
        actor,
        audit.Action.COLOUR_DELETED,
        target=colour,
        summary=f"Renk silindi: {colour.name}",
        before={"name": colour.name, "hex_code": colour.hex_code},
    )
    colour.delete()


@transaction.atomic
def update_colour(
    colour: Colour,
    *,
    name: str | None = None,
    hex_code: str | None = None,
    is_active: bool | None = None,
) -> Colour:
    """The SKU code is fixed once created, so existing SKUs stay valid."""
    _rename(colour, name)
    if hex_code is not None:
        if not HEX_RE.match(hex_code):
            raise _invalid("hex_code", "Renk kodu #RRGGBB biçiminde olmalı.")
        colour.hex_code = hex_code.upper()
    if is_active is not None:
        colour.is_active = is_active
    colour.save()
    return colour


@transaction.atomic
def update_category(category: Category, *, name: str) -> Category:
    name = name.strip()
    if not name:
        raise _invalid("name", "Ad girin.")
    category.name = name
    category.save(update_fields=["name"])
    return category
