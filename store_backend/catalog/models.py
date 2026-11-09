"""Store-specific catalog data, kept beside Oscar's catalogue models.

Oscar owns Product, Category, ProductClass and StockRecord (the basket and order
engine need them). A Product here is an Oscar *parent* Product; each Variant is an
Oscar *child* Product with a stock record. The models below hang the store's own
fields off them one-to-one, so Oscar's tables are never forked.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
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

    class Meta:
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
