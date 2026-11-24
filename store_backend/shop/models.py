from __future__ import annotations

import unicodedata

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

# The fixed Legal pages. Their text lives in `LegalPageVersion` rows (an edit adds the
# next version, never changes one); `services.legal_pages` knows each page's current
# version, so checkout can record which versions a Shopper accepted.
LEGAL_PAGES: tuple[str, ...] = (
    "distance_sales_contract",
    "pre_information_form",
    "returns_withdrawal",
    "privacy_notice",
    "delivery_shipping",
    "payment_methods",
    "about_contact",
)


# The original public address (slug) and title of each page. The Owner may change a slug
# later (`LegalPageSlug` holds the live and the old ones); these are where each starts.
LEGAL_PAGE_META: dict[str, tuple[str, str]] = {
    "distance_sales_contract": (
        "mesafeli-satis-sozlesmesi",
        "Mesafeli Satış Sözleşmesi",
    ),
    "pre_information_form": ("on-bilgilendirme-formu", "Ön Bilgilendirme Formu"),
    "returns_withdrawal": ("iade-ve-cayma-kosullari", "İade ve Cayma Koşulları"),
    "privacy_notice": ("gizlilik-ve-kvkk", "Gizlilik ve KVKK Aydınlatma Metni"),
    "delivery_shipping": ("teslimat-ve-kargo", "Teslimat ve Kargo"),
    "payment_methods": ("odeme-yontemleri", "Ödeme Yöntemleri"),
    "about_contact": ("hakkimizda-iletisim", "Hakkımızda / İletişim"),
}


def default_legal_versions() -> dict[str, str]:
    """Kept for migration 0001, which refers to it by name."""
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

    stock_alerts_enabled = models.BooleanField(
        default=False,
        help_text="Stock alerts are commercial messages (ETK): leave off until the "
        "Owner has confirmed the İYS registration.",
    )

    # Superseded by `BankAccount`; no longer read or written. The columns stay for one
    # release so the previous release still runs on this database, then are dropped.
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_holder = models.CharField(max_length=150, blank=True)
    bank_iban = models.CharField(max_length=34, blank=True)

    notification_emails = models.JSONField(default=list, blank=True)

    # The home page banner now lives in Banners (`home_hero`). Unused; drop these in the
    # release after this one, with the old bank columns above.
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


class BankAccount(models.Model):
    """One account a Shopper may pay a bank transfer into, shown in the order's
    confirmation and the payment emails. The Owner keeps the list in Settings; its order
    is the order the Shopper sees."""

    name = models.CharField(max_length=100)
    account_holder = models.CharField(max_length=150)
    iban = models.CharField(max_length=34)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return f"{self.name} {self.iban}"


USER_AGENT_MAX = 500


class ImmutableError(Exception):
    """A saved Legal page version is never changed or removed."""


class LegalPageVersionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableError

    def delete(self):
        raise ImmutableError

    def bulk_update(self, objs, fields, batch_size=None):
        raise ImmutableError


class LegalPageVersion(models.Model):
    """One saved text of a Legal page. An edit inserts the next version.

    A Consent record points at (page, version), so the text a Shopper accepted can
    always be shown again. Immutable here (not a database trigger: the guard is the
    model and queryset, and no endpoint changes or deletes one).
    """

    page = models.CharField(max_length=40, choices=[(p, p) for p in LEGAL_PAGES])
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Empty for the text carried over from the first release.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LegalPageVersionQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "version"],
                name="legal_page_version_unique",
            ),
        ]
        ordering = ["page", "-version"]

    def __str__(self) -> str:
        return f"{self.page} v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError


class LegalPageSlug(models.Model):
    """One public address of a Legal page. A page has exactly one current slug; the
    ones it had before stay as rows that redirect to the current one, so a link in an
    old email or contract keeps working. The page key, its versions and Consent records
    never move."""

    page = models.CharField(max_length=40, choices=[(p, p) for p in LEGAL_PAGES])
    slug = models.SlugField(max_length=100, unique=True)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["page"],
                condition=models.Q(is_current=True),
                name="shop_legal_slug_one_current_per_page",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.page}: {self.slug}"


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


class RefundMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Havale/EFT"
    CASH = "cash", "Nakit"


class OrderRecord(models.Model):
    """What Staff recorded against an Order that Oscar's Order has no place for.

    Created on first use (`order_ops.record_of`), so Orders placed before any
    Dashboard action need no row. Oscar's own `status` holds the Fulfilment status
    and its payment Sources hold the Payment status.
    """

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="record",
    )
    # Payment
    paid_amount = _money(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Cancellation
    cancel_reason = models.CharField(max_length=500, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Shipping
    carrier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # Returns and refund
    return_requested_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    refund_amount = _money(null=True, blank=True)
    refund_method = models.CharField(
        max_length=20,
        choices=RefundMethod,
        blank=True,
    )
    refund_iban = models.CharField(max_length=34, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    # Invoice details the buyer asked for
    buyer_tax_number = models.CharField(max_length=50, blank=True)
    company_title = models.CharField(max_length=200, blank=True)

    def __str__(self) -> str:
        return f"Record of order {self.order_id}"


class InvoiceStatus(models.TextChoices):
    NOT_ISSUED = "not_issued", "Kesilmedi"
    ISSUED = "issued", "Kesildi"
    CANCELLED = "cancelled", "İptal edildi"


class Invoice(models.Model):
    """What Staff typed in after issuing the invoice by hand in the GIB portal.

    The document itself is never stored. Created on first save; an Order without a
    row has not been invoiced.
    """

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="invoice",
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus,
        default=InvoiceStatus.NOT_ISSUED,
    )
    number = models.CharField(max_length=50, blank=True)
    date = models.DateField(null=True, blank=True)
    credit_note_number = models.CharField(max_length=50, blank=True)
    credit_note_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["number"],
                condition=~models.Q(number=""),
                name="invoice_number_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"Invoice {self.number or '-'} of order {self.order_id}"


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
    # The Campaign that priced the line, frozen with it (ADR 0009): the link for the
    # Owner's reporting, and the name, percentage and Shelf price as they were then.
    # PROTECT: a Campaign an Order used is never deleted (ADR 0005).
    campaign = models.ForeignKey(
        "store_catalog.Campaign",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    campaign_name = models.CharField(max_length=200, blank=True)
    campaign_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    shelf_unit_price = _money(null=True, blank=True)

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


def province_key(name: str) -> str:
    """Fold a province name for lookup: case, dotted/dotless i and diacritics.

    Python's own lower() turns "I" into "i" and "İ" into "i̇", so the Turkish
    letters are mapped by hand first; everything else is NFKD with marks removed.
    """
    text = name.replace("İ", "i").replace("I", "i").replace("ı", "i").casefold()
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.split())


class Carrier(models.Model):
    """A shipping company the shop prices against (Yurtiçi, Aras, PTT)."""

    name = models.CharField(max_length=64, unique=True)
    position = models.PositiveSmallIntegerField(
        default=0,
        help_text="Tie-break when two carriers cost the same: lower wins.",
    )

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.name


class Zone(models.Model):
    """A distance band from Mersin that groups provinces for pricing."""

    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["number"]

    def __str__(self) -> str:
        return f"Bölge {self.number}"


class ProvinceZone(models.Model):
    """Which Zone a province (il) belongs to. `key` is the folded name for lookups."""

    name = models.CharField(max_length=64, unique=True)
    key = models.CharField(max_length=64, unique=True, editable=False)
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name="provinces")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.key = province_key(self.name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "key"}
        super().save(*args, **kwargs)


class RateRow(models.Model):
    """One carrier's KDV-inclusive TRY prices for one Zone, by desi band."""

    carrier = models.ForeignKey(Carrier, on_delete=models.CASCADE, related_name="rates")
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="rates")
    band_0_2 = _money()
    band_3_5 = _money()
    band_6_10 = _money()
    per_extra_desi = _money(help_text="For each desi above 10.")

    class Meta:
        ordering = ["zone__number", "carrier__position", "carrier_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "zone"],
                name="one_rate_row_per_carrier_zone",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.carrier} / {self.zone}"


class ReturnReason(models.TextChoices):
    WRONG_SIZE = "wrong_size", "Beden uymadı"
    CHANGED_MIND = "changed_mind", "Vazgeçtim"
    DEFECTIVE = "defective", "Ürün kusurlu"
    WRONG_ITEM = "wrong_item", "Yanlış ürün gönderildi"
    OTHER = "other", "Diğer"


class ReturnStatus(models.TextChoices):
    REQUESTED = "requested", "Talep edildi"
    APPROVED = "approved", "Onaylandı"
    REJECTED = "rejected", "Reddedildi"
    RECEIVED = "received", "Teslim alındı"
    REFUNDED = "refunded", "İade edildi"


class ReturnRequest(models.Model):
    """A Shopper's request to send back some lines of a delivered Order.

    Has its own status so a partial return never changes the Order's Fulfilment
    status; the Order-level `return_requested` and `returned` belong to Staff's
    whole-Order flow in `order_ops`.
    """

    order = models.ForeignKey(
        "order.Order",
        on_delete=models.PROTECT,
        related_name="return_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=ReturnStatus,
        default=ReturnStatus.REQUESTED,
    )
    reason = models.CharField(max_length=20, choices=ReturnReason)
    note = models.CharField(max_length=500, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=500, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    refund_amount = _money(null=True, blank=True)
    refund_method = models.CharField(
        max_length=20,
        choices=RefundMethod,
        blank=True,
    )
    refund_iban = models.CharField(max_length=34, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at", "-pk")

    def __str__(self) -> str:
        return f"Return request {self.pk} for order {self.order_id}"


class ReturnLine(models.Model):
    request = models.ForeignKey(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    order_line = models.ForeignKey(
        "order.Line",
        on_delete=models.PROTECT,
        related_name="return_lines",
    )
    quantity = models.PositiveIntegerField()
    restocked = models.BooleanField(
        default=False,
        help_text="Staff put the returned units back on the shelf.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "order_line"],
                name="one_return_line_per_order_line",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x line {self.order_line_id}"


class Favorite(models.Model):
    """A Shopper's heart on a Product (not a Variant). Our own model, not Oscar's
    wishlists (ADR 0003): no counts, no sharing, no notifications."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    product = models.ForeignKey(
        "catalogue.Product",
        on_delete=models.CASCADE,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="one_favorite_per_user_and_product",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.product_id}"


class StockAlert(models.Model):
    """A live request to be emailed once when a sold-out Variant is back.

    Deleted when the email is sent, rejected, lapsed or the account is deleted. The
    proof of consent lives on in `AlertConsent`. `expires_at` is 48 h after
    subscribing while unconfirmed and 90 days after confirming.
    """

    email = models.EmailField()
    variant = models.ForeignKey(
        "catalogue.Product",
        on_delete=models.CASCADE,
        related_name="+",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="stock_alerts",
    )
    notice_version = models.CharField(
        max_length=10,
        help_text="Version of the privacy notice the Shopper saw when subscribing.",
    )
    confirm_token = models.CharField(max_length=64, unique=True)
    confirm_sent_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                "variant",
                name="one_stock_alert_per_email_and_variant",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} -> variant {self.variant_id}"


class AlertConsent(models.Model):
    """The record that a Shopper agreed to a Stock alert email. Kept 3 years.

    Separate from `StockAlert` so it outlives the live row. The Variant link is
    nulled, not cascaded, if the Variant is deleted.
    """

    class HowGiven(models.TextChoices):
        CONFIRM_LINK = "confirm_link", "Onay bağlantısı"
        SIGNED_IN = "signed_in", "Oturum açmış müşteri"

    email = models.EmailField()
    variant = models.ForeignKey(
        "catalogue.Product",
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    consented_at = models.DateTimeField()
    notice_version = models.CharField(max_length=10)
    how_given = models.CharField(max_length=20, choices=HowGiven.choices)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-consented_at", "-pk"]
        indexes = [models.Index(fields=["consented_at"])]

    def __str__(self) -> str:
        return f"{self.email} -> variant {self.variant_id}"


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Onay bekliyor"
    PUBLISHED = "published", "Yayında"
    REJECTED = "rejected", "Reddedildi"


class Review(models.Model):
    """A verified buyer's star rating, with optional moderated text (ADR 0004).

    Our own model, not Oscar's reviews (ADR 0003). `body` is the raw markdown
    source the Shopper typed; the Storefront renders it without HTML.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_reviews",
    )
    product = models.ForeignKey(
        "catalogue.Product",
        on_delete=models.CASCADE,
        related_name="+",
    )
    stars = models.PositiveSmallIntegerField()
    body = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ReviewStatus,
        default=ReviewStatus.PUBLISHED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="one_review_per_user_and_product",
            ),
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5),
                name="review_stars_1_to_5",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.stars}★ by {self.user_id} on {self.product_id}"


# --- menus (docs/adr/0007) -------------------------------------------------------

# Built-in Storefront pages a Menu item may point to: key -> (label, path).
MENU_BUILTIN_PAGES: dict[str, tuple[str, str]] = {
    "home": ("Ana sayfa", "/"),
    "order_tracking": ("Sipariş takibi", "/siparis-takip"),
    "favorites": ("Favorilerim", "/hesap/favoriler"),
    "basket": ("Sepet", "/sepet"),
    "legal_search": ("Yasal sayfalarda ara", "/yasal"),
    "campaigns": ("Kampanyalar", "/kampanyalar"),
    "contact": ("İletişim", "/iletisim"),
}


class Menu(models.Model):
    """One of the two fixed navigation lists: no API creates or deletes a Menu."""

    class Key(models.TextChoices):
        HEADER = "header", "Üst menü"
        FOOTER = "footer", "Alt menü"

    key = models.CharField(max_length=20, choices=Key, unique=True)

    def __str__(self) -> str:
        return self.key


class MenuItem(models.Model):
    """One entry in a Menu. It points at its target by reference, so its label and link
    follow the target; a Category is protected from deletion while an item points at it.
    """

    class Target(models.TextChoices):
        CATEGORY = "category", "Kategori"
        LEGAL_PAGE = "legal_page", "Yasal sayfa"
        PRODUCT = "product", "Ürün"
        BRAND = "brand", "Marka"
        COLLECTION = "collection", "Koleksiyon"
        CAMPAIGN_PAGE = "campaign_page", "Kampanya sayfası"
        BUILTIN = "builtin", "Hazır sayfa"
        URL = "url", "Özel bağlantı"
        GROUP = "group", "Grup başlığı"

    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    position = models.PositiveIntegerField(default=0)
    target_type = models.CharField(max_length=20, choices=Target)
    category = models.ForeignKey(
        "catalogue.Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    include_subcategories = models.BooleanField(default=False)
    product = models.ForeignKey(
        "catalogue.Product",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    brand = models.ForeignKey(
        "store_catalog.Brand",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    collection = models.ForeignKey(
        "store_catalog.Collection",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    campaign_page = models.ForeignKey(
        "store_catalog.CampaignPage",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    legal_page = models.CharField(max_length=40, blank=True)
    builtin = models.CharField(max_length=40, blank=True)
    url = models.CharField(max_length=500, blank=True)
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Overrides the target's own label; also names a group heading.",
    )
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return f"{self.menu_id}:{self.target_type}:{self.label}"


class ContactCategory(models.Model):
    """A topic the Shopper picks on the contact form. Owner-managed; one that has
    threads is deactivated instead of deleted."""

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        verbose_name_plural = "contact categories"
        constraints = [
            models.UniqueConstraint(Lower("name"), name="contact_category_name_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class ContactThreadStatus(models.TextChoices):
    NEW = "new", "Yeni"
    IN_PROGRESS = "in_progress", "Devam ediyor"
    ANSWERED = "answered", "Yanıtlandı"


class ContactThread(models.Model):
    """One conversation started from the contact form. The Shopper's message and
    later Staff drafts and replies are its `ContactMessage` rows, in order."""

    category = models.ForeignKey(
        ContactCategory,
        on_delete=models.PROTECT,
        related_name="threads",
    )
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The signed-in Shopper who wrote it, if any.",
    )
    order_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="What the Shopper typed, kept as typed (upper-cased).",
    )
    order = models.ForeignKey(
        "order.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Set only when the typed number matches an Order.",
    )
    status = models.CharField(
        max_length=20,
        choices=ContactThreadStatus,
        default=ContactThreadStatus.NEW,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(auto_now_add=True)
    anonymised_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_activity_at", "-pk"]
        indexes = [models.Index(fields=["status", "-last_activity_at"])]

    def __str__(self) -> str:
        return f"Thread {self.pk} ({self.status})"


class ContactMessageKind(models.TextChoices):
    SHOPPER = "shopper", "Müşteri"
    REPLY = "reply", "Yanıt"


class ContactReplyState(models.TextChoices):
    """Where a reply is on its way to the Shopper. Shopper messages have no state."""

    DRAFT = "draft", "Taslak"
    PENDING = "pending", "Onay bekliyor"
    REJECTED = "rejected", "Reddedildi"
    SENT = "sent", "Gönderildi"


class ContactMessage(models.Model):
    thread = models.ForeignKey(
        ContactThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    kind = models.CharField(max_length=20, choices=ContactMessageKind)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Replies only: the author, the Owner's decision and the text before an edit.
    state = models.CharField(
        max_length=20,
        choices=ContactReplyState,
        blank=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    rejection_note = models.TextField(blank=True)
    drafted_body = models.TextField(
        blank=True,
        help_text="The Staff text, kept when the Owner edits it before sending.",
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.kind} message in thread {self.thread_id}"
