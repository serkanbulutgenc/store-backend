# ruff: noqa: EM101, TRY003
"""Checkout: turn a basket into an Order, in one transaction.

`place_order` validates the form, takes row locks on the basket and on every stock
record it needs (in primary-key order, so concurrent checkouts cannot deadlock), checks
stock under those locks, then writes the Order through Oscar's `OrderCreator` together
with its Consent record, Pre-information snapshot and KDV snapshots. A stock shortfall
raises a 409 before anything is written. The basket is never touched on failure.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import phonenumbers
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from oscar.apps.address.models import Country
from oscar.apps.basket.models import Basket as BasketModel
from oscar.apps.order.models import BillingAddress
from oscar.apps.order.models import Order
from oscar.apps.order.models import ShippingAddress
from oscar.apps.order.models import Surcharge
from oscar.apps.order.utils import OrderCreator
from oscar.apps.partner.models import StockRecord
from oscar.apps.payment.models import Source
from oscar.apps.payment.models import SourceType
from oscar.core.prices import Price

from store_backend.api.errors import BadRequestError
from store_backend.api.errors import ConflictError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.strategy import CURRENCY
from store_backend.services import catalog
from store_backend.services import pricing
from store_backend.services import shop_settings
from store_backend.shop.models import ConsentRecord
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import OrderLineSnapshot
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import PreInformationSnapshot

if TYPE_CHECKING:
    from oscar.apps.basket.models import Line as BasketLine

    from store_backend.shop.models import StoreSettings
    from store_backend.users.models import User

STATUS_NEW = "new"
STATUS_PREPARING = "preparing"
SHIPPING_CODE = "flat"
SHIPPING_NAME = "Standart kargo"

# No vowels or look-alikes (0/O, 1/I): easy to read out and to type into a transfer.
NUMBER_ALPHABET = "23456789BCDFGHJKLMNPQRSTVWXYZ"
NUMBER_LENGTH = 6
NUMBER_PREFIX = "AT-"

MIN_NAME_PARTS = 2
ADDRESS_PARTS = ("il", "ilce", "mahalle", "street")
SOURCE_TYPES = {
    PaymentMethod.BANK_TRANSFER: "Havale/EFT",
    PaymentMethod.COD: "Kapıda ödeme",
}


@dataclass(frozen=True)
class Address:
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str = ""


@dataclass(frozen=True)
class CheckoutForm:
    full_name: str
    email: str
    phone: str
    shipping_address: Address
    billing_address: Address | None
    payment_method: PaymentMethod
    contract_acknowledged: bool
    cod_fee_consent: bool = False
    marketing_consent: bool = False


@dataclass(frozen=True)
class Caller:
    user: User | None
    ip_address: str | None
    user_agent: str


@dataclass(frozen=True)
class _Fees:
    shipping: pricing.LineSnapshot
    cod: pricing.LineSnapshot | None


class _ShippingMethod:
    """The flat fee as Oscar's `OrderCreator` wants to see a shipping method."""

    name = SHIPPING_NAME
    code = SHIPPING_CODE

    def discount(self, basket: BasketModel) -> Decimal:
        return pricing.ZERO


class _SnapshotOrderCreator(OrderCreator):
    """Builds Order lines from the KDV snapshots, so stored totals equal their sum.

    Oscar's strategy prices are per unit; splitting each line total once (see
    `pricing`) is what keeps stored line amounts, snapshots and the Order total equal.
    """

    def __init__(self, snapshots: dict[int, pricing.LineSnapshot]) -> None:
        self.snapshots = snapshots

    def create_additional_line_models(self, order, order_line, basket_line):
        snap = self.snapshots[basket_line.pk]
        order_line.line_price_incl_tax = snap.total_incl
        order_line.line_price_excl_tax = snap.total_excl
        order_line.line_price_before_discounts_incl_tax = snap.total_incl
        order_line.line_price_before_discounts_excl_tax = snap.total_excl
        order_line.unit_price_incl_tax = snap.unit_price_incl
        order_line.unit_price_excl_tax = pricing.kurus(
            snap.total_excl / snap.quantity,
        )
        order_line.save()
        OrderLineSnapshot.objects.create(
            line=order_line,
            quantity=snap.quantity,
            unit_price_incl=snap.unit_price_incl,
            kdv_rate=snap.kdv_rate,
            total_incl=snap.total_incl,
            total_excl=snap.total_excl,
            total_kdv=snap.total_kdv,
        )


# --- validation ---------------------------------------------------------------


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _clean_phone(raw: str) -> str:
    msg = "Geçerli bir telefon numarası girin."
    try:
        parsed = phonenumbers.parse(raw.strip(), "TR")
    except phonenumbers.NumberParseException:
        raise _invalid("phone", msg) from None
    if not phonenumbers.is_valid_number(parsed):
        raise _invalid("phone", msg)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _clean_address(prefix: str, address: Address) -> Address:
    parts = {part: getattr(address, part).strip() for part in ADDRESS_PARTS}
    errors = {
        f"{prefix}.{part}": ["Bu alan zorunludur."]
        for part, value in parts.items()
        if not value
    }
    if errors:
        raise UnprocessableError(fields=errors)
    return Address(**parts, postal_code=address.postal_code.strip())


def _clean(form: CheckoutForm) -> CheckoutForm:
    name = " ".join(form.full_name.split())
    if len(name.split()) < MIN_NAME_PARTS:
        raise _invalid("full_name", "Ad ve soyadınızı girin.")
    email = form.email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise _invalid("email", "Geçerli bir e-posta adresi girin.") from None
    phone = _clean_phone(form.phone)
    shipping = _clean_address("shipping_address", form.shipping_address)
    billing = (
        _clean_address("billing_address", form.billing_address)
        if form.billing_address
        else shipping
    )
    if not form.contract_acknowledged:
        raise _invalid(
            "contract_acknowledged",
            "Devam etmek için mesafeli satış sözleşmesini onaylamalısınız.",
        )
    if form.payment_method == PaymentMethod.COD and not form.cod_fee_consent:
        raise _invalid(
            "cod_fee_consent",
            "Kapıda ödeme bedelini kabul etmelisiniz.",
        )
    return CheckoutForm(
        full_name=name,
        email=email,
        phone=phone,
        shipping_address=shipping,
        billing_address=billing,
        payment_method=form.payment_method,
        contract_acknowledged=True,
        cod_fee_consent=form.cod_fee_consent,
        marketing_consent=form.marketing_consent,
    )


# --- stock --------------------------------------------------------------------


def _lock_basket(basket: BasketModel) -> list[BasketLine]:
    if basket.id is None:
        raise BadRequestError("Sepetiniz boş.", code="basket_empty")
    BasketModel.objects.select_for_update().filter(pk=basket.pk).first()
    lines = list(
        basket.lines.select_related(
            "product__variant_profile__colour",
            "product__parent__profile",
            "product__parent__product_class__profile",
        ).order_by("date_created", "pk"),
    )
    if not lines:
        raise BadRequestError("Sepetiniz boş.", code="basket_empty")
    return lines


def _locked_records(lines: list[BasketLine]) -> dict[int, StockRecord]:
    """The first stock record of each Variant, locked in primary-key order."""
    records: dict[int, StockRecord] = {}
    locked = (
        StockRecord.objects.select_for_update()
        .filter(product_id__in=[line.product_id for line in lines])
        .order_by("pk")
    )
    for record in locked:
        records.setdefault(record.product_id, record)  # lowest pk, as the strategy
    return records


def _check_stock(lines: list[BasketLine], records: dict[int, StockRecord]) -> None:
    """Raise a 409 naming the first problem; nothing has been written yet."""
    problems: list[tuple[str, str]] = []
    for line in lines:
        variant = line.product
        title = variant.parent.title
        record = records.get(variant.pk)
        if variant.parent.profile.status != ProductStatus.PUBLISHED:
            problems.append(("unavailable", f"{title} artık satışta değil."))
            continue
        units = (
            max(record.num_in_stock - (record.num_allocated or 0), 0) if record else 0
        )
        if units == 0:
            problems.append(("sold_out", f"{title} tükendi."))
        elif line.quantity > units:
            problems.append(
                (
                    "insufficient_stock",
                    f"{title} için stokta en fazla {units} adet var.",
                ),
            )
    if problems:
        message = " ".join(m for _, m in problems)
        raise ConflictError(
            message,
            code=problems[0][0],
            fields={"basket": [m for _, m in problems]},
        )


# --- totals -------------------------------------------------------------------


def _snapshots(lines: list[BasketLine]) -> dict[int, pricing.LineSnapshot]:
    return {
        line.pk: pricing.line_snapshot(
            unit_price=catalog.shelf_price_for(line.product),
            quantity=line.quantity,
            kdv_rate=catalog.kdv_rate_for(line.product),
        )
        for line in lines
    }


def _fees(
    items_total: Decimal,
    method: PaymentMethod,
    store: StoreSettings,
) -> _Fees:
    rate = store.fee_kdv_rate
    threshold = store.free_shipping_threshold
    free = threshold is not None and items_total >= threshold
    shipping = pricing.fee_line(
        pricing.ZERO if free else store.shipping_fee,
        kdv_rate=rate,
    )
    cod = (
        pricing.fee_line(store.cod_fee, kdv_rate=rate)
        if method == PaymentMethod.COD
        else None
    )
    return _Fees(shipping=shipping, cod=cod)


# --- records ------------------------------------------------------------------


def _order_number() -> str:
    while True:
        code = "".join(secrets.choice(NUMBER_ALPHABET) for _ in range(NUMBER_LENGTH))
        number = NUMBER_PREFIX + code
        if not Order.objects.filter(number=number).exists():
            return number


def _address_fields(form: CheckoutForm, address: Address) -> dict:
    first, _, last = form.full_name.rpartition(" ")
    country, _ = Country.objects.get_or_create(
        iso_3166_1_a2="TR",
        defaults={
            "iso_3166_1_a3": "TUR",
            "iso_3166_1_numeric": "792",
            "printable_name": "Türkiye",
            "name": "Türkiye",
            "is_shipping_country": True,
        },
    )
    return {
        "first_name": first,
        "last_name": last,
        "line1": address.street,
        "line3": address.mahalle,
        "line4": address.ilce,
        "state": address.il,
        "postcode": address.postal_code,
        "country": country,
    }


def _line_text(line: BasketLine, snap: pricing.LineSnapshot) -> list[str]:
    variant = line.product
    profile = variant.variant_profile
    text = [
        (
            f"- {variant.parent.title} ({profile.colour.name}, {profile.size}) "
            f"x {snap.quantity}: birim {tl(snap.unit_price_incl)}, "
            f"toplam {tl(snap.total_incl)} (KDV %{snap.kdv_rate:g} dahil)"
        ),
    ]
    if variant.parent.product_class.profile.is_hygiene:
        text.append(
            "  Hijyen ürünü: ambalajı açıldıktan sonra iade edilemez.",
        )
    return text


def tl(amount: Decimal) -> str:
    """Turkish money text: `1.234,50 TL`."""
    whole, _, cents = f"{Decimal(amount):.2f}".partition(".")
    return f"{int(whole):,}".replace(",", ".") + f",{cents} TL"


def _pre_information(  # noqa: PLR0913
    *,
    number: str,
    placed_at,
    form: CheckoutForm,
    store: StoreSettings,
    lines: list[BasketLine],
    snapshots: dict[int, pricing.LineSnapshot],
    fees: _Fees,
    totals: pricing.OrderTotals,
) -> str:
    """The text the Shopper was shown, with this Order's items, prices and fees."""
    shipping = form.shipping_address
    billing = form.billing_address or shipping

    def where(a: Address) -> str:
        postcode = f" {a.postal_code}" if a.postal_code else ""
        return f"{a.street}, {a.mahalle}, {a.ilce}/{a.il}{postcode}"

    out = [
        "ÖN BİLGİLENDİRME FORMU",
        f"Sipariş no: {number}",
        f"Tarih: {timezone.localtime(placed_at):%d.%m.%Y %H:%M}",
        "",
        "SATICI",
        f"{store.seller_name}",
        f"Vergi/MERSİS no: {store.seller_tax_number}",
        f"Adres: {store.seller_address}",
        f"E-posta: {store.seller_email}  Telefon: {store.seller_phone}",
        f"İade için kargo: {store.return_carrier}",
        f"Şikayet iletişim: {store.complaint_contact}",
        "",
        "ALICI",
        f"{form.full_name}, {form.email}, {form.phone}",
        f"Teslimat adresi: {where(shipping)}",
        f"Fatura adresi: {where(billing)}",
        "",
        "ÜRÜNLER",
    ]
    for line in lines:
        out.extend(_line_text(line, snapshots[line.pk]))
    items = sum((snapshots[line.pk].total_incl for line in lines), pricing.ZERO)
    out.append("")
    out.append(f"Ürünler toplamı: {tl(items)}")
    out.append(f"Kargo ücreti: {tl(fees.shipping.total_incl)}")
    if fees.cod:
        out.append(f"Kapıda ödeme bedeli: {tl(fees.cod.total_incl)}")
    out.append(f"Toplam (KDV dahil): {tl(totals.total_incl)}")
    out.append(f"Toplam KDV: {tl(totals.total_kdv)}")
    out.append(f"Ödeme yöntemi: {PaymentMethod(form.payment_method).label}")
    out.extend(
        [
            "",
            "CAYMA HAKKI",
            (
                "Teslim tarihinden itibaren 14 gün içinde gerekçe göstermeden "
                "cayma hakkınızı kullanabilirsiniz. Ayrıntılar iade ve cayma "
                "koşulları sayfasındadır."
            ),
            "",
            "YASAL METİN SÜRÜMLERİ",
            *(f"{page}: {ver}" for page, ver in sorted(store.legal_versions.items())),
        ],
    )
    return "\n".join(out)


def _record_payment(order: Order, method: PaymentMethod, total: Decimal) -> None:
    source_type, _ = SourceType.objects.get_or_create(
        code=method.value,
        defaults={"name": SOURCE_TYPES[method]},
    )
    # Nothing is debited yet: bank transfer waits for Staff, COD for delivery.
    Source.objects.create(
        order=order,
        source_type=source_type,
        currency=CURRENCY,
        amount_allocated=total,
    )


def _write_fee_snapshots(order: Order, fees: _Fees) -> None:
    for kind, fee in (
        (OrderFeeSnapshot.Kind.SHIPPING, fees.shipping),
        (OrderFeeSnapshot.Kind.COD, fees.cod),
    ):
        if fee is None or fee.total_incl == 0:
            continue
        OrderFeeSnapshot.objects.create(
            order=order,
            kind=kind,
            kdv_rate=fee.kdv_rate,
            total_incl=fee.total_incl,
            total_excl=fee.total_excl,
            total_kdv=fee.total_kdv,
        )
        if kind == OrderFeeSnapshot.Kind.COD:
            Surcharge.objects.create(
                order=order,
                name="Kapıda ödeme bedeli",
                code="cod",
                incl_tax=fee.total_incl,
                excl_tax=fee.total_excl,
            )


# --- the use case -------------------------------------------------------------


@transaction.atomic
def place_order(basket: BasketModel, raw_form: CheckoutForm, caller: Caller) -> Order:
    form = _clean(raw_form)
    lines = _lock_basket(basket)
    _check_stock(lines, _locked_records(lines))

    store = shop_settings.get()
    snapshots = _snapshots(lines)
    items_total = sum((s.total_incl for s in snapshots.values()), pricing.ZERO)
    fees = _fees(items_total, form.payment_method, store)
    every = [*snapshots.values(), fees.shipping, *([fees.cod] if fees.cod else [])]
    totals = pricing.order_totals(every)

    number = _order_number()
    placed_at = timezone.now()
    cod = form.payment_method == PaymentMethod.COD
    shipping_address = ShippingAddress.objects.create(
        phone_number=form.phone,
        **_address_fields(form, form.shipping_address),
    )
    billing_address = BillingAddress.objects.create(
        **_address_fields(form, form.billing_address or form.shipping_address),
    )
    creator = _SnapshotOrderCreator(snapshots)
    order = creator.place_order(
        basket=basket,
        total=Price(CURRENCY, excl_tax=totals.total_excl, tax=totals.total_kdv),
        shipping_method=_ShippingMethod(),
        shipping_charge=Price(
            CURRENCY,
            excl_tax=fees.shipping.total_excl,
            tax=fees.shipping.total_kdv,
        ),
        user=caller.user,
        shipping_address=shipping_address,
        billing_address=billing_address,
        order_number=number,
        status=STATUS_PREPARING if cod else STATUS_NEW,
        guest_email=form.email,
    )
    _write_fee_snapshots(order, fees)
    _record_payment(order, form.payment_method, totals.total_incl)
    ConsentRecord.objects.create(
        order=order,
        contract_acknowledged=True,
        cod_fee_consent=form.cod_fee_consent,
        marketing_consent_at=placed_at if form.marketing_consent else None,
        legal_versions=store.legal_versions,
        ip_address=caller.ip_address,
        user_agent=caller.user_agent,
    )
    PreInformationSnapshot.objects.create(
        order=order,
        text=_pre_information(
            number=number,
            placed_at=placed_at,
            form=form,
            store=store,
            lines=lines,
            snapshots=snapshots,
            fees=fees,
            totals=totals,
        ),
    )
    basket.submit()
    return order
