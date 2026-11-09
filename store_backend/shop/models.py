from __future__ import annotations

from django.db import models

# The fixed Legal pages. The text lives in the frontend repo as versioned Markdown;
# the backend only knows each page's current version identifier, so checkout can record
# which versions a Shopper accepted.
LEGAL_PAGES: tuple[str, ...] = (
    "distance_sales_contract",
    "pre_information_form",
    "returns_withdrawal",
    "privacy_notice",
    "delivery_shipping",
    "payment_methods",
    "about_contact",
)


def default_legal_versions() -> dict[str, str]:
    return dict.fromkeys(LEGAL_PAGES, "1")


def _money(**kwargs) -> models.DecimalField:
    return models.DecimalField(max_digits=10, decimal_places=2, **kwargs)


class StoreSettings(models.Model):
    """The single Owner-edited record: always row 1, via `shop_settings.get`."""

    # Charges. Amounts are KDV-inclusive TRY.
    shipping_fee = _money(default=0)
    free_shipping_threshold = _money(
        null=True,
        blank=True,
        help_text="Baskets at or above this get free shipping. Empty: never free.",
    )
    cod_fee = _money(default=0)
    fee_kdv_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=20,
        help_text="KDV rate for shipping and COD lines (pending accountant).",
    )
    low_stock_default = models.PositiveIntegerField(default=2)

    # Bank transfer details shown to Shoppers who pick transfer.
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_holder = models.CharField(max_length=150, blank=True)
    bank_iban = models.CharField(max_length=34, blank=True)

    notification_emails = models.JSONField(default=list, blank=True)

    banner_image_url = models.CharField(max_length=500, blank=True)
    banner_link = models.CharField(max_length=500, blank=True)
    banner_alt = models.CharField(max_length=200, blank=True)

    # Seller identity (distance-sales law).
    seller_name = models.CharField(max_length=200, blank=True)
    seller_tax_number = models.CharField(max_length=50, blank=True)
    seller_address = models.TextField(blank=True)
    seller_email = models.EmailField(blank=True)
    seller_phone = models.CharField(max_length=30, blank=True)
    return_carrier = models.CharField(max_length=100, blank=True)
    complaint_contact = models.CharField(max_length=300, blank=True)

    legal_versions = models.JSONField(default=default_legal_versions)

    class Meta:
        verbose_name_plural = "store settings"

    def __str__(self) -> str:
        return "Store settings"
