# ruff: noqa: EM101, TRY003
"""Checkout: turn a basket into an Order, in one transaction.

`place_order` validates the form, takes row locks on the basket and on every stock
record it needs (in primary-key order, so concurrent checkouts cannot deadlock), checks
stock under those locks, prices the basket with `quote.quote`, then `place` writes the
Order (via `order_creation`) together with its Consent record and Pre-information
snapshot. A stock shortfall raises a 409 before anything is written. The basket is
never touched on failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from oscar.apps.basket.models import Basket as BasketModel

from store_backend.api.errors import BadRequestError
from store_backend.api.errors import ConflictError
from store_backend.catalog.models import ProductStatus
from store_backend.services import claiming
from store_backend.services import order_creation
from store_backend.services import outbox
from store_backend.services import pre_information
from store_backend.services import quote as quoting
from store_backend.services import shop_settings
from store_backend.services import stock
from store_backend.services.contact import MIN_NAME_PARTS
from store_backend.services.contact import Address
from store_backend.services.contact import clean_address
from store_backend.services.contact import clean_phone
from store_backend.services.contact import invalid
from store_backend.shop.models import ConsentRecord
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import PreInformationSnapshot

if TYPE_CHECKING:
    from oscar.apps.basket.models import Line as BasketLine
    from oscar.apps.order.models import Order
    from oscar.apps.partner.models import StockRecord

    from store_backend.services.quote import Quote
    from store_backend.shop.models import StoreSettings
    from store_backend.users.models import User

STATUS_NEW = "new"
STATUS_PREPARING = "preparing"
PAYMENT_UNPAID = "unpaid"


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
    legal_versions: dict[str, str] | None = None  # what the Shopper was shown


@dataclass(frozen=True)
class Caller:
    user: User | None
    ip_address: str | None
    user_agent: str


class _Problem(NamedTuple):
    code: str
    message: str


# --- validation ---------------------------------------------------------------


def _validated(form: CheckoutForm) -> CheckoutForm:
    name = " ".join(form.full_name.split())
    if len(name.split()) < MIN_NAME_PARTS:
        raise invalid("full_name", "Ad ve soyadınızı girin.")
    email = form.email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise invalid("email", "Geçerli bir e-posta adresi girin.") from None
    phone = clean_phone(form.phone)
    shipping = clean_address("shipping_address", form.shipping_address)
    billing = (
        clean_address("billing_address", form.billing_address)
        if form.billing_address
        else shipping
    )
    if not form.contract_acknowledged:
        raise invalid(
            "contract_acknowledged",
            "Devam etmek için mesafeli satış sözleşmesini onaylamalısınız.",
        )
    if form.payment_method == PaymentMethod.COD and not form.cod_fee_consent:
        raise invalid(
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
        contract_acknowledged=form.contract_acknowledged,
        cod_fee_consent=form.cod_fee_consent,
        marketing_consent=form.marketing_consent,
        legal_versions=form.legal_versions,
    )


def _check_legal_versions(form: CheckoutForm, store: StoreSettings) -> None:
    """The consent record must describe what the Shopper saw, not what is current."""
    seen = form.legal_versions or {}
    stale = [
        page for page, ver in seen.items() if store.legal_versions.get(page) != ver
    ]
    if stale:
        raise ConflictError(
            "Yasal metinler güncellendi. Sayfayı yenileyip yeniden onaylayın.",
            code="legal_changed",
            fields={"legal_versions": stale},
        )


# --- stock --------------------------------------------------------------------


def _lock_basket(basket: BasketModel) -> list[BasketLine]:
    lines: list[BasketLine] = []
    if basket.id is not None:
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


def _check_stock(lines: list[BasketLine], records: dict[int, StockRecord]) -> None:
    """Raise a 409 naming the first problem; nothing has been written yet."""
    problems: list[_Problem] = []
    for line in lines:
        variant = line.product
        title = variant.parent.title
        free = stock.units(records.get(variant.pk))
        if variant.parent.profile.status != ProductStatus.PUBLISHED:
            problems.append(_Problem("unavailable", f"{title} artık satışta değil."))
        elif free == 0:
            problems.append(_Problem("sold_out", f"{title} tükendi."))
        elif line.quantity > free:
            problems.append(
                _Problem(
                    "insufficient_stock",
                    f"{title} için stokta en fazla {free} adet var.",
                ),
            )
    if problems:
        raise ConflictError(
            " ".join(p.message for p in problems),
            code=problems[0].code,
            fields={"basket": [p.message for p in problems]},
        )


def place(
    basket: BasketModel,
    quote: Quote,
    form: CheckoutForm,
    caller: Caller,
) -> Order:
    """Write the Order for a validated form and the quote of the locked basket."""
    store = quote.store
    placed_at = timezone.now()
    order = order_creation.create_order(
        basket=basket,
        quote=quote,
        method=form.payment_method,
        status=(
            STATUS_PREPARING if form.payment_method == PaymentMethod.COD else STATUS_NEW
        ),
        user=caller.user or claiming.verified_owner(form.email),
        email=form.email,
        full_name=form.full_name,
        phone=form.phone,
        shipping=form.shipping_address,
        billing=form.billing_address or form.shipping_address,
    )
    ConsentRecord.objects.create(
        order=order,
        contract_acknowledged=form.contract_acknowledged,
        cod_fee_consent=form.cod_fee_consent,
        marketing_consent_at=placed_at if form.marketing_consent else None,
        legal_versions=store.legal_versions,
        ip_address=caller.ip_address,
        user_agent=caller.user_agent,
    )
    PreInformationSnapshot.objects.create(
        order=order,
        text=pre_information.render(
            number=order.number,
            placed_at=placed_at,
            form=form,
            store=store,
            quote=quote,
        ),
    )
    basket.submit()
    outbox.notify_shopper(order, "order_placed")
    outbox.notify_staff(
        "staff_new_order",
        {
            "number": order.number,
            "total": pre_information.format_try(order.total_incl_tax),
            "payment": PaymentMethod(form.payment_method).label,
            "item_count": sum(line.quantity for line in order.lines.all()),
            "admin_url": outbox.admin_url(f"orders/{order.number}"),
        },
    )
    return order


@transaction.atomic
def place_order(basket: BasketModel, raw_form: CheckoutForm, caller: Caller) -> Order:
    form = _validated(raw_form)
    lines = _lock_basket(basket)
    _check_stock(
        lines,
        stock.records_for([line.product_id for line in lines], lock=True),
    )
    store = shop_settings.get()
    _check_legal_versions(form, store)
    quote = quoting.quote(lines, form.payment_method, store)
    return place(basket, quote, form, caller)
