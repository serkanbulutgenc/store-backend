"""Store-specific catalog data, kept beside Oscar's catalogue models.

Oscar owns Product, Category, ProductClass and StockRecord (the basket and order
engine need them). A Product here is an Oscar *parent* Product; each Variant is an
Oscar *child* Product with a stock record. The models below hang the store's own
fields off them one-to-one, so Oscar's tables are never forked.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product

from store_backend.catalog.sizes import SizeScheme

DEFAULT_LOW_STOCK_THRESHOLD = 2


def money_field(**kwargs) -> models.DecimalField:
    return models.DecimalField(max_digits=10, decimal_places=2, **kwargs)


class ProductClassProfile(models.Model):
    """What a Product class fixes: size scheme, default KDV rate, hygiene flag."""

    product_class = models.OneToOneField(
        "catalogue.ProductClass",
        on_delete=models.CASCADE,
        related_name="profile",
    )
    size_scheme = models.CharField(
        max_length=16,
        choices=[(s.value, s.value) for s in SizeScheme],
    )
    default_kdv_rate = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0)), MaxValueValidator(Decimal(100))],
        help_text="Percent, e.g. 10.00 or 20.00.",
    )
    is_hygiene = models.BooleanField(
        default=False,
        help_text="Not returnable once opened.",
    )
    default_desi = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Carrier size of one item of this class, in desi.",
    )

    def __str__(self) -> str:
        return str(self.product_class)


class Colour(models.Model):
    """An entry in the Owner-managed shared colour list."""

    name = models.CharField(max_length=64, unique=True)
    code = models.CharField(
        max_length=3,
        unique=True,
        help_text="Three letters used in SKUs.",
    )
    hex_code = models.CharField(max_length=7, help_text="Swatch, e.g. #C0392B.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=128, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Supplier(models.Model):
    """Internal only: never exposed on the Storefront."""

    name = models.CharField(max_length=128, unique=True)
    code = models.CharField(max_length=32, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class ProductProfile(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    slug = models.SlugField(max_length=255, unique=True)
    status = models.CharField(
        max_length=16,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
    )
    brand = models.ForeignKey(
        Brand,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    supplier_code = models.CharField(max_length=64, blank=True)
    cost_price = money_field(null=True, blank=True)
    shelf_price = money_field(help_text="KDV-inclusive, TRY.")
    old_price = money_field(null=True, blank=True)
    kdv_rate = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Overrides the Product class default (accessories).",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=DEFAULT_LOW_STOCK_THRESHOLD,
    )
    desi = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Overrides the Product class default desi.",
    )
    # Storefront search; maintained only by `services.search.reindex`.
    search_text = models.TextField(blank=True, editable=False)
    search_vector = SearchVectorField(null=True, editable=False)

    class Meta:
        indexes = [
            GinIndex(fields=["search_vector"], name="catalog_search_vector_gin"),
            GinIndex(
                fields=["search_text"],
                name="catalog_search_text_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(shelf_price__gte=0),
                name="catalog_shelf_price_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(old_price__isnull=True)
                | Q(old_price__gt=models.F("shelf_price")),
                name="catalog_old_price_above_shelf",
            ),
        ]

    def __str__(self) -> str:
        return self.product.title


class SlugRedirect(models.Model):
    """An old Product slug that now points at its Product."""

    old_slug = models.SlugField(max_length=255, unique=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="slug_redirects",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.old_slug} -> {self.product_id}"


class VariantProfile(models.Model):
    """Colour x size of one Variant (an Oscar child Product)."""

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="variant_profile",
    )
    parent = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="The Product.",
    )
    colour = models.ForeignKey(Colour, on_delete=models.PROTECT, related_name="+")
    size = models.CharField(max_length=16)
    sku = models.CharField(max_length=64, unique=True)
    barcode = models.CharField(max_length=64, null=True, blank=True, unique=True)
    price_override = money_field(null=True, blank=True, help_text="KDV-inclusive.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "colour", "size"],
                name="catalog_variant_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.sku


class ProductPhoto(models.Model):
    """A photo of a Product, optionally tagged with a Colour.

    Colour-less images are shared by every colour. The original upload is kept as
    is; WebP derivatives are made by a background job. Files are named by us (a UUID
    for the original, a content hash for derivatives), never by the uploader.
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="photos",
    )
    colour = models.ForeignKey(
        Colour,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    alt_text = models.CharField(
        max_length=255,
        blank=True,
        help_text="Empty means product name and colour.",
    )
    original = models.CharField(max_length=255, help_text="Storage name.")
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    derivatives = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"width": 400, "name": "<storage name>"}, ...]',
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(is_primary=True),
                name="catalog_one_primary_photo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id}:{self.original}"

    @property
    def effective_alt_text(self) -> str:
        if self.alt_text:
            return self.alt_text
        parts = [self.product.title]
        if self.colour is not None:
            parts.append(self.colour.name)
        return " ".join(parts)

    def file_names(self) -> list[str]:
        return [self.original, *(d["name"] for d in self.derivatives)]

    def derivative_urls(self) -> list[dict]:
        from store_backend.services.media import media_url  # noqa: PLC0415

        return [
            {"width": d["width"], "url": media_url(d["name"])} for d in self.derivatives
        ]


class CategoryProfile(models.Model):
    """What the store adds to an Oscar Category: copy, image, SEO, order, visibility.

    Created with the Category (see signals). The image uses the same pipeline and
    derivative sizes as product photos; it lives here as columns, not a table, since a
    Category has at most one.
    """

    class ImageStatus(models.TextChoices):
        NONE = "", "None"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    category = models.OneToOneField(
        Category,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    description = models.TextField(blank=True)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Position among siblings; ties fall back to the name.",
    )
    is_hidden = models.BooleanField(
        default=False,
        help_text="Hides the Category and its subtree from the Storefront.",
    )
    image_original = models.CharField(max_length=255, blank=True)
    image_width = models.PositiveIntegerField(default=0)
    image_height = models.PositiveIntegerField(default=0)
    image_status = models.CharField(
        max_length=16,
        choices=ImageStatus.choices,
        blank=True,
        default="",
    )
    image_derivatives = models.JSONField(default=list, blank=True)
    image_alt = models.CharField(max_length=255, blank=True)
    image_error = models.TextField(blank=True)

    def __str__(self) -> str:
        return str(self.category)

    def image_file_names(self) -> list[str]:
        if not self.image_original:
            return []
        return [self.image_original, *(d["name"] for d in self.image_derivatives)]

    def image_derivative_urls(self) -> list[dict]:
        from store_backend.services.media import media_url  # noqa: PLC0415

        return [
            {"width": d["width"], "url": media_url(d["name"])}
            for d in self.image_derivatives
        ]


class PendingFileRemoval(models.Model):
    """A stored file waiting out the grace period before it is deleted from disk."""

    name = models.CharField(max_length=255)
    remove_after = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return self.name


class AttributeKind(models.TextChoices):
    TEXT = "text", "Metin"
    OPTION = "option", "Seçenek listesi"


class Attribute(models.Model):
    """A descriptive property of a Product class, such as Kumaş or Sezon.

    The Owner defines it; Staff fill in its values on each Product. Our own model, not
    Oscar's `ProductAttribute` (ADR 0003). `kind` and `is_multiple` are fixed when the
    attribute is created, because changing them would orphan the stored values.
    """

    product_class = models.ForeignKey(
        "catalogue.ProductClass",
        on_delete=models.CASCADE,
        related_name="spec_attributes",
    )
    name = models.CharField(max_length=64)
    kind = models.CharField(max_length=8, choices=AttributeKind.choices)
    is_multiple = models.BooleanField(
        default=False,
        help_text="A Product may pick several options (option lists only).",
    )
    is_filterable = models.BooleanField(
        default=False,
        help_text="Shown as a filter on category and search pages (option lists only).",
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                "product_class",
                Lower("name"),
                name="catalog_attribute_name_per_class",
            ),
            models.CheckConstraint(
                condition=Q(kind="option") | Q(is_multiple=False, is_filterable=False),
                name="catalog_attribute_text_is_plain",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_class}: {self.name}"


class AttributeOption(models.Model):
    """One choice of an option-list Attribute. Never shared between attributes."""

    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.CASCADE,
        related_name="options",
    )
    label = models.CharField(max_length=64)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(
                "attribute",
                Lower("label"),
                name="catalog_option_label_per_attribute",
            ),
        ]

    def __str__(self) -> str:
        return self.label


class AttributeValue(models.Model):
    """A Product's value for one Attribute: free text, or one row per chosen option."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="spec_values",
    )
    attribute = models.ForeignKey(
        Attribute,
        on_delete=models.CASCADE,
        related_name="values",
    )
    option = models.ForeignKey(
        AttributeOption,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="values",
    )
    text = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "product",
                "attribute",
                condition=Q(option__isnull=True),
                name="catalog_value_one_text_per_attribute",
            ),
            models.UniqueConstraint(
                "product",
                "attribute",
                "option",
                condition=Q(option__isnull=False),
                name="catalog_value_one_row_per_option",
            ),
            models.CheckConstraint(
                condition=Q(option__isnull=True) | Q(text=""),
                name="catalog_value_option_has_no_text",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} {self.attribute_id}"
