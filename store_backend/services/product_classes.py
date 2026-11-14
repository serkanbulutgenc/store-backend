# ruff: noqa: EM101, TRY003
"""Product classes: the Owner creates, edits and deletes them.

A class fixes its size scheme, default KDV rate, hygiene flag and default desi. The size
scheme is locked once any Product uses the class, so no Variant is left with a size its
class no longer allows. A Product without its own KDV rate follows the class default, so
changing the default first pins the old rate on those Products: the new rate applies to
Products created afterwards only (order lines snapshot their rate anyway).
"""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductClassProfile
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.sizes import SizeScheme
from store_backend.services import audit
from store_backend.services.shipping_admin import validate_desi

if TYPE_CHECKING:
    from store_backend.users.models import User

MAX_KDV_RATE = Decimal(100)
CENTS = Decimal("0.01")
FIELDS = ("name", "size_scheme", "default_kdv_rate", "is_hygiene", "default_desi")


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _name(value: object) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        raise _invalid("name", "Sınıf adı girin.")
    if len(name) > 128:  # noqa: PLR2004
        raise _invalid("name", "Sınıf adı en fazla 128 karakter olabilir.")
    return name


def _scheme(value: object) -> str:
    if value not in {s.value for s in SizeScheme}:
        raise _invalid("size_scheme", "Geçerli bir beden düzeni seçin.")
    return str(value)


def _kdv(value: object) -> Decimal:
    try:
        rate = Decimal(str(value)).quantize(CENTS)
    except InvalidOperation, ValueError:
        raise _invalid("default_kdv_rate", "KDV oranı bir sayı olmalı.") from None
    if not Decimal(0) <= rate <= MAX_KDV_RATE:
        raise _invalid("default_kdv_rate", "KDV oranı 0 ile 100 arasında olmalı.")
    return rate


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid(field, "Evet ya da hayır olmalı.")
    return value


def _check_name_free(name: str, *, exclude_id: int | None = None) -> None:
    clash = ProductClass.objects.filter(name__iexact=name)
    if exclude_id is not None:
        clash = clash.exclude(pk=exclude_id)
    if clash.exists():
        msg = "Bu ürün sınıfı zaten var."
        raise ConflictError(msg, code="product_class_taken", fields={"name": [msg]})


def _snapshot(pc: ProductClass) -> dict:
    p = pc.profile
    return {
        "name": pc.name,
        "size_scheme": p.size_scheme,
        "default_kdv_rate": str(p.default_kdv_rate),
        "is_hygiene": p.is_hygiene,
        "default_desi": p.default_desi,
    }


def product_count(class_id: int) -> int:
    return Product.objects.filter(product_class_id=class_id).count()


def _get_locked(class_id: int) -> ProductClass:
    pc = (
        ProductClass.objects.select_for_update(of=("self",))
        .select_related("profile")
        .filter(pk=class_id)
        .first()
    )
    if pc is None:
        raise NotFoundError("Ürün sınıfı bulunamadı.")
    return pc


@transaction.atomic
def create(  # noqa: PLR0913
    actor: User,
    *,
    name: object,
    size_scheme: object,
    default_kdv_rate: object,
    is_hygiene: object = False,
    default_desi: object = 1,
) -> ProductClass:
    clean = _name(name)
    _check_name_free(clean)
    values = {
        "size_scheme": _scheme(size_scheme),
        "default_kdv_rate": _kdv(default_kdv_rate),
        "is_hygiene": _bool(is_hygiene, "is_hygiene"),
        "default_desi": validate_desi(default_desi, "default_desi"),
    }
    try:
        with transaction.atomic():
            pc = ProductClass.objects.create(name=clean)
    except IntegrityError:  # the generated slug collides with another class
        msg = "Bu ürün sınıfı zaten var."
        raise ConflictError(
            msg,
            code="product_class_taken",
            fields={"name": [msg]},
        ) from None
    ProductClassProfile.objects.create(product_class=pc, **values)
    pc = ProductClass.objects.select_related("profile").get(pk=pc.pk)
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=pc.profile,
        summary=f"Ürün sınıfı oluşturuldu: {pc.name}",
        after=_snapshot(pc),
    )
    return pc


def _apply(pc: ProductClass, profile: ProductClassProfile, changes: dict) -> None:
    """Validate and set each given field; the caller saves the profile."""
    if "name" in changes:
        name = _name(changes["name"])
        if name != pc.name:
            _check_name_free(name, exclude_id=pc.pk)
            pc.name = name
            pc.save(update_fields=["name"])
    if "size_scheme" in changes:
        scheme = _scheme(changes["size_scheme"])
        if scheme != profile.size_scheme:
            if product_count(pc.pk):
                msg = "Bu sınıfta ürün var; beden düzeni değiştirilemez."
                raise ConflictError(
                    msg,
                    code="size_scheme_locked",
                    fields={"size_scheme": [msg]},
                )
            profile.size_scheme = scheme
    if "default_kdv_rate" in changes:
        rate = _kdv(changes["default_kdv_rate"])
        if rate != profile.default_kdv_rate:
            ProductProfile.objects.filter(
                product__product_class=pc,
                kdv_rate__isnull=True,
            ).update(kdv_rate=profile.default_kdv_rate)
            profile.default_kdv_rate = rate
    if "is_hygiene" in changes:
        profile.is_hygiene = _bool(changes["is_hygiene"], "is_hygiene")
    if "default_desi" in changes:
        profile.default_desi = validate_desi(changes["default_desi"], "default_desi")


@transaction.atomic
def update(actor: User, class_id: int, changes: dict) -> ProductClass:
    """Apply the given fields. A size scheme change is refused while Products exist."""
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    pc = _get_locked(class_id)
    profile = pc.profile
    before = _snapshot(pc)

    _apply(pc, profile, changes)
    profile.save()

    after = _snapshot(pc)
    if after != before:
        audit.record(
            actor,
            audit.Action.SETTINGS_CHANGE,
            target=profile,
            summary=f"Ürün sınıfı düzenlendi: {pc.name}",
            before=before,
            after=after,
        )
    return pc


@transaction.atomic
def delete(actor: User, class_id: int) -> None:
    pc = _get_locked(class_id)
    count = product_count(pc.pk)
    if count:
        msg = f"Bu sınıfta {count} ürün var; önce ürünleri başka sınıfa taşıyın."
        raise ConflictError(msg, code="product_class_in_use")
    before = _snapshot(pc)
    profile = pc.profile
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=profile,
        summary=f"Ürün sınıfı silindi: {pc.name}",
        before=before,
    )
    pc.delete()  # the profile cascades
