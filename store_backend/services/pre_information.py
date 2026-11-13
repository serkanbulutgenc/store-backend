"""The Pre-information text frozen with each Order.

Built from the same figures the Order is written from, so the text and the Order totals
cannot disagree.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.utils import timezone

from store_backend.services import legal_pages
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import PaymentMethod

if TYPE_CHECKING:
    from datetime import datetime

    from oscar.apps.basket.models import Line as BasketLine

    from store_backend.services.checkout import CheckoutForm
    from store_backend.services.contact import Address
    from store_backend.services.pricing import LineSnapshot
    from store_backend.services.quote import Quote
    from store_backend.shop.models import StoreSettings

WITHDRAWAL = (
    "Teslim tarihinden itibaren 14 gün içinde gerekçe göstermeden cayma hakkınızı "
    "kullanabilirsiniz. Ayrıntılar iade ve cayma koşulları sayfasındadır."
)
HYGIENE_NOTE = "  Hijyen ürünü: ambalajı açıldıktan sonra iade edilemez."


def format_try(amount: Decimal) -> str:
    """Turkish money text: `1.234,50 TL`."""
    whole, _, cents = f"{Decimal(amount):.2f}".partition(".")
    return f"{int(whole):,}".replace(",", ".") + f",{cents} TL"


def _where(address: Address) -> str:
    postcode = f" {address.postal_code}" if address.postal_code else ""
    return f"{address.street}, {address.mahalle}, {address.ilce}/{address.il}{postcode}"


def _seller(store: StoreSettings) -> list[str]:
    return [
        store.seller_name,
        f"Vergi/MERSİS no: {store.seller_tax_number}",
        f"Adres: {store.seller_address}",
        f"E-posta: {store.seller_email}  Telefon: {store.seller_phone}",
        f"İade için kargo: {store.return_carrier}",
        f"Şikayet iletişim: {store.complaint_contact}",
    ]


def _line(line: BasketLine, snap: LineSnapshot) -> list[str]:
    variant = line.product
    profile = variant.variant_profile
    text = [
        (
            f"- {variant.parent.title} ({profile.colour.name}, {profile.size}) "
            f"x {snap.quantity}: birim {format_try(snap.unit_price_incl)}, "
            f"toplam {format_try(snap.total_incl)} (KDV %{snap.kdv_rate:g} dahil)"
        ),
    ]
    if variant.parent.product_class.profile.is_hygiene:
        text.append(HYGIENE_NOTE)
    return text


def render(
    *,
    number: str,
    placed_at: datetime,
    form: CheckoutForm,
    store: StoreSettings,
    quote: Quote,
) -> str:
    """What the Shopper was shown, with this Order's items, prices and fees."""
    out = [
        "ÖN BİLGİLENDİRME FORMU",
        f"Sipariş no: {number}",
        f"Tarih: {timezone.localtime(placed_at):%d.%m.%Y %H:%M}",
        "",
        "SATICI",
        *_seller(store),
        "",
        "ALICI",
        f"{form.full_name}, {form.email}, {form.phone}",
        f"Teslimat adresi: {_where(form.shipping_address)}",
        f"Fatura adresi: {_where(form.billing_address or form.shipping_address)}",
        "",
        "ÜRÜNLER",
    ]
    for line in quote.lines:
        out.extend(_line(line, quote.snapshots[line.pk]))
    out.extend(
        [
            "",
            f"Ürünler toplamı: {format_try(quote.items_total)}",
            f"Kargo ücreti: {format_try(quote.fees.shipping.total_incl)}",
        ],
    )
    if quote.fees.cod:
        label = OrderFeeSnapshot.Kind.COD.label
        out.append(f"{label}: {format_try(quote.fees.cod.total_incl)}")
    out.extend(
        [
            f"Toplam (KDV dahil): {format_try(quote.totals.total_incl)}",
            f"Toplam KDV: {format_try(quote.totals.total_kdv)}",
            f"Ödeme yöntemi: {PaymentMethod(form.payment_method).label}",
            "",
            "CAYMA HAKKI",
            WITHDRAWAL,
            "",
            "YASAL METİN SÜRÜMLERİ",
            *(
                f"{page}: {ver}"
                for page, ver in sorted(legal_pages.current_versions().items())
            ),
        ],
    )
    return "\n".join(out)
