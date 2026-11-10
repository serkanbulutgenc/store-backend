from __future__ import annotations

from django.conf import settings
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(pk=1),
                name="store_settings_singleton",
            ),
        ]

    def __str__(self) -> str:
        return "Store settings"


USER_AGENT_MAX = 500


class PaymentMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Havale/EFT"
    COD = "cod", "Kapıda ödeme"


class ConsentRecord(models.Model):
    """What the Shopper agreed to at checkout, with proof of when and from where."""

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="consent",
    )
    contract_acknowledged = models.BooleanField()
    cod_fee_consent = models.BooleanField(default=False)
    marketing_consent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the optional marketing box was ticked.",
    )
    legal_versions = models.JSONField(
        help_text="Version of every Legal page current at placement.",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=USER_AGENT_MAX, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Consent for order {self.order_id}"


class PreInformationSnapshot(models.Model):
    """The frozen pre-information text shown for this Order. Never edited."""

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="pre_information",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Pre-information for order {self.order_id}"


class _KdvSnapshot(models.Model):
    kdv_rate = models.DecimalField(max_digits=5, decimal_places=2)
    total_incl = _money()
    total_excl = _money()
    total_kdv = _money()

    class Meta:
        abstract = True


class OrderLineSnapshot(_KdvSnapshot):
    """An Order line's KDV split, frozen at placement and never recomputed."""

    line = models.OneToOneField(
        "order.Line",
        on_delete=models.PROTECT,
        related_name="kdv_snapshot",
    )
    quantity = models.PositiveIntegerField()
    unit_price_incl = _money()

    def __str__(self) -> str:
        return f"KDV snapshot of line {self.line_id}"


class OrderFeeSnapshot(_KdvSnapshot):
    """The KDV split of a shipping or COD fee line."""

    class Kind(models.TextChoices):
        SHIPPING = "shipping", "Kargo"
        COD = "cod", "Kapıda ödeme bedeli"

    order = models.ForeignKey(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="fee_snapshots",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "kind"],
                name="one_fee_snapshot_per_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} fee of order {self.order_id}"


MAX_SAVED_ADDRESSES = 5


class SavedAddress(models.Model):
    """One entry of a Customer's address book: up to five, exactly one default."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_addresses",
    )
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30)
    il = models.CharField(max_length=100)
    ilce = models.CharField(max_length=100)
    mahalle = models.CharField(max_length=150)
    street = models.CharField(max_length=200)
    postal_code = models.CharField(max_length=10, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="one_default_address_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.full_name}, {self.ilce}/{self.il}"


class AccountDeletionRequest(models.Model):
    """A Customer's request to delete their account, waiting in the Owner queue."""

    class Status(models.TextChoices):
        PENDING = "pending", "Bekliyor"
        COMPLETED = "completed", "Tamamlandı"
        DECLINED = "declined", "Reddedildi"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deletion_requests",
    )
    email = models.EmailField(help_text="The account email when the request was made.")
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending"),
                name="one_pending_deletion_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Deletion request {self.pk} ({self.status})"
