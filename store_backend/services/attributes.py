# ruff: noqa: EM101, EM102, TRY003
"""Attributes: the Owner defines them per Product class, Staff fill in the values.

An Attribute is a text field or an option list (single or multi value, fixed when it is
created). Values are optional, so adding an attribute backfills nothing and an empty one
is never shown. Deleting an attribute or option that is in use removes its values from
Products: that is a Destructive action, so the API demands `confirm` and the
Dashboard says how many Products are affected. Search text and the storefront facets
read these values; `search.reindex` is called whenever they change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count
from django.db.models import Max
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import ConflictError
from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Attribute
from store_backend.catalog.models import AttributeKind
from store_backend.catalog.models import AttributeOption
from store_backend.catalog.models import AttributeValue
from store_backend.services import audit
from store_backend.services import search

if TYPE_CHECKING:
    from collections.abc import Iterable

    from store_backend.users.models import User

MAX_NAME = 64
MAX_TEXT = 500


def _invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def _taken(field: str, message: str) -> ConflictError:
    return ConflictError(message, code="attribute_taken", fields={field: [message]})


def _clean(value: object, field: str, label: str, limit: int = MAX_NAME) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid(field, f"{label} girin.")
    if len(text) > limit:
        raise _invalid(field, f"{label} en fazla {limit} karakter olabilir.")
    return text


# --- reading -----------------------------------------------------------------


def _attributes(**lookup) -> list[Attribute]:
    return list(
        Attribute.objects.filter(**lookup)
        .prefetch_related("options")
        .annotate(product_count=Count("values__product", distinct=True))
        .order_by("sort_order", "pk"),
    )


def attribute_dict(a: Attribute) -> dict:
    return {
        "id": a.pk,
        "product_class_id": a.product_class_id,
        "name": a.name,
        "kind": a.kind,
        "is_multiple": a.is_multiple,
        "is_filterable": a.is_filterable,
        "sort_order": a.sort_order,
        "options": [{"id": o.pk, "label": o.label} for o in a.options.all()],
        "product_count": getattr(a, "product_count", 0),
    }


def list_for_class(class_id: int) -> list[dict]:
    if not ProductClass.objects.filter(pk=class_id).exists():
        raise NotFoundError("Ürün sınıfı bulunamadı.")
    return [attribute_dict(a) for a in _attributes(product_class_id=class_id)]


def get(attribute_id: int) -> dict:
    found = _attributes(pk=attribute_id)
    if not found:
        raise NotFoundError("Özellik bulunamadı.")
    return attribute_dict(found[0])


def _attribute(attribute_id: int) -> Attribute:
    attribute = Attribute.objects.select_for_update().filter(pk=attribute_id).first()
    if attribute is None:
        raise NotFoundError("Özellik bulunamadı.")
    return attribute


def _option(option_id: int) -> AttributeOption:
    option = (
        AttributeOption.objects.select_for_update(of=("self",))
        .select_related("attribute")
        .filter(pk=option_id)
        .first()
    )
    if option is None:
        raise NotFoundError("Seçenek bulunamadı.")
    return option


# --- definitions (Owner) -------------------------------------------------------


def _record(actor: User, target, summary: str, before=None, after=None) -> None:
    audit.record(
        actor,
        audit.Action.SETTINGS_CHANGE,
        target=target,
        summary=summary,
        before=before,
        after=after,
    )


def _labels(options: Iterable[object]) -> list[str]:
    labels: list[str] = []
    for raw in options:
        label = _clean(raw, "options", "Seçenek adı")
        if label.lower() in {x.lower() for x in labels}:
            raise _invalid("options", f"“{label}” birden fazla kez girilmiş.")
        labels.append(label)
    return labels


@transaction.atomic
def create(  # noqa: PLR0913
    actor: User,
    class_id: int,
    *,
    name: object,
    kind: object,
    is_multiple: bool = False,
    is_filterable: bool = False,
    options: Iterable[object] = (),
) -> dict:
    if not ProductClass.objects.select_for_update().filter(pk=class_id).exists():
        raise NotFoundError("Ürün sınıfı bulunamadı.")
    clean = _clean(name, "name", "Özellik adı")
    if kind not in set(AttributeKind.values):
        raise _invalid("kind", "Özellik türü metin ya da seçenek listesi olmalı.")
    labels = _labels(options)
    if kind == AttributeKind.TEXT and (is_multiple or is_filterable or labels):
        raise _invalid(
            "kind",
            "Metin özelliği çoklu, filtrelenebilir ya da seçenekli olamaz.",
        )
    last = Attribute.objects.filter(product_class_id=class_id).aggregate(
        top=Max("sort_order"),
    )["top"]
    try:
        with transaction.atomic():
            attribute = Attribute.objects.create(
                product_class_id=class_id,
                name=clean,
                kind=kind,
                is_multiple=is_multiple,
                is_filterable=is_filterable,
                sort_order=0 if last is None else last + 1,
            )
    except IntegrityError:
        raise _taken("name", "Bu sınıfta bu adla bir özellik zaten var.") from None
    AttributeOption.objects.bulk_create(
        AttributeOption(attribute=attribute, label=label, sort_order=i)
        for i, label in enumerate(labels)
    )
    out = get(attribute.pk)
    _record(actor, attribute, f"Özellik oluşturuldu: {clean}", after=out)
    return out


@transaction.atomic
def update(actor: User, attribute_id: int, changes: dict) -> dict:
    """Rename, reorder or toggle filtering. Kind and multi-value are fixed."""
    unknown = set(changes) - {"name", "is_filterable", "sort_order"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bu alan sonradan değiştirilemez.")
    attribute = _attribute(attribute_id)
    before = get(attribute_id)
    if "name" in changes:
        attribute.name = _clean(changes["name"], "name", "Özellik adı")
    if "is_filterable" in changes:
        flag = changes["is_filterable"]
        if flag and attribute.kind != AttributeKind.OPTION:
            raise _invalid(
                "is_filterable",
                "Yalnızca seçenek listeleri filtrelenebilir.",
            )
        attribute.is_filterable = bool(flag)
    if "sort_order" in changes:
        order = changes["sort_order"]
        if not isinstance(order, int) or order < 0:
            raise _invalid("sort_order", "Sıra negatif olamayan bir sayı olmalı.")
        attribute.sort_order = order
    try:
        with transaction.atomic():
            attribute.save()
    except IntegrityError:
        raise _taken("name", "Bu sınıfta bu adla bir özellik zaten var.") from None
    out = get(attribute_id)
    if out != before:
        _record(actor, attribute, f"Özellik düzenlendi: {attribute.name}", before, out)
    return out


def _confirm_or_refuse(count: int, what: str, confirm: bool) -> None:  # noqa: FBT001
    if count and not confirm:
        raise ConflictError(
            f"{what} {count} üründe kullanılıyor; silinirse bu ürünlerdeki değerler de "
            "silinir. Devam etmek için onaylayın.",
            code="confirmation_required",
        )


@transaction.atomic
def delete(actor: User, attribute_id: int, *, confirm: bool = False) -> None:
    attribute = _attribute(attribute_id)
    before = get(attribute_id)
    _confirm_or_refuse(before["product_count"], f"“{attribute.name}” özelliği", confirm)
    product_ids = _product_ids(attribute=attribute)
    _record(actor, attribute, f"Özellik silindi: {attribute.name}", before=before)
    attribute.delete()  # options and values cascade
    search.reindex_where(product_id__in=product_ids)


# --- options (Owner) -----------------------------------------------------------


def _option_dict(option: AttributeOption) -> dict:
    return {"id": option.pk, "label": option.label}


@transaction.atomic
def add_option(actor: User, attribute_id: int, label: object) -> dict:
    attribute = _attribute(attribute_id)
    if attribute.kind != AttributeKind.OPTION:
        raise _invalid("label", "Metin özelliğine seçenek eklenemez.")
    clean = _clean(label, "label", "Seçenek adı")
    last = attribute.options.aggregate(top=Max("sort_order"))["top"]
    try:
        with transaction.atomic():
            option = AttributeOption.objects.create(
                attribute=attribute,
                label=clean,
                sort_order=0 if last is None else last + 1,
            )
    except IntegrityError:
        raise _taken("label", "Bu seçenek zaten var.") from None
    _record(actor, attribute, f"Seçenek eklendi: {attribute.name} › {clean}")
    return _option_dict(option)


@transaction.atomic
def update_option(actor: User, option_id: int, changes: dict) -> dict:
    unknown = set(changes) - {"label", "sort_order"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "Bilinmeyen alan.")
    option = _option(option_id)
    old = option.label
    if "label" in changes:
        option.label = _clean(changes["label"], "label", "Seçenek adı")
    if "sort_order" in changes:
        order = changes["sort_order"]
        if not isinstance(order, int) or order < 0:
            raise _invalid("sort_order", "Sıra negatif olamayan bir sayı olmalı.")
        option.sort_order = order
    try:
        with transaction.atomic():
            option.save()
    except IntegrityError:
        raise _taken("label", "Bu seçenek zaten var.") from None
    if option.label != old:
        search.reindex_where(product_id__in=_product_ids(option=option))
        _record(
            actor,
            option.attribute,
            f"Seçenek yeniden adlandırıldı: {option.attribute.name}",
            before={"label": old},
            after={"label": option.label},
        )
    return _option_dict(option)


@transaction.atomic
def delete_option(actor: User, option_id: int, *, confirm: bool = False) -> None:
    option = _option(option_id)
    count = option.values.values("product").distinct().count()
    _confirm_or_refuse(count, f"“{option.label}” seçeneği", confirm)
    product_ids = _product_ids(option=option)
    _record(
        actor,
        option.attribute,
        f"Seçenek silindi: {option.attribute.name} › {option.label}",
        before={"label": option.label, "product_count": count},
    )
    option.delete()
    search.reindex_where(product_id__in=product_ids)


def _product_ids(*, attribute: Attribute | None = None, option=None) -> list[int]:
    values = AttributeValue.objects.all()
    if attribute is not None:
        values = values.filter(attribute=attribute)
    if option is not None:
        values = values.filter(option=option)
    return list(values.values_list("product_id", flat=True).distinct())


# --- values (Staff) ------------------------------------------------------------


@dataclass(frozen=True)
class ValueInput:
    attribute_id: int
    text: str | None = None
    option_ids: tuple[int, ...] = ()


def values_for(product: Product) -> list[dict]:
    """The Product's stored values, one entry per attribute that has any."""
    out: dict[int, dict] = {}
    rows = AttributeValue.objects.filter(product=product).order_by("pk")
    for row in rows:
        entry = out.setdefault(
            row.attribute_id,
            {"attribute_id": row.attribute_id, "text": None, "option_ids": []},
        )
        if row.option_id is None:
            entry["text"] = row.text
        else:
            entry["option_ids"].append(row.option_id)
    return list(out.values())


def _rows_for(attribute: Attribute, item: ValueInput, product: Product) -> list:
    if attribute.kind == AttributeKind.TEXT:
        if item.option_ids:
            raise _invalid("values", f"“{attribute.name}” bir metin özelliği.")
        text = (item.text or "").strip()
        if len(text) > MAX_TEXT:
            raise _invalid(
                "values",
                f"“{attribute.name}” en fazla {MAX_TEXT} karakter olabilir.",
            )
        return (
            [AttributeValue(product=product, attribute=attribute, text=text)]
            if text
            else []
        )
    if (item.text or "").strip():
        raise _invalid("values", f"“{attribute.name}” bir seçenek listesi.")
    ids = list(dict.fromkeys(item.option_ids))
    if len(ids) > 1 and not attribute.is_multiple:
        raise _invalid("values", f"“{attribute.name}” için tek seçenek seçilebilir.")
    valid = set(attribute.options.filter(pk__in=ids).values_list("pk", flat=True))
    if valid != set(ids):
        raise _invalid("values", f"“{attribute.name}” için geçersiz seçenek.")
    return [
        AttributeValue(product=product, attribute=attribute, option_id=i) for i in ids
    ]


@transaction.atomic
def set_values(product: Product, items: Iterable[ValueInput]) -> list[dict]:
    """Replace the values of the attributes named in `items`; others are untouched.

    An empty text or no options clears that attribute. Attributes must belong to the
    Product's class.
    """
    items = list(items)
    seen = [i.attribute_id for i in items]
    if len(seen) != len(set(seen)):
        raise _invalid("values", "Bir özellik birden fazla kez gönderildi.")
    attributes = {
        a.pk: a
        for a in Attribute.objects.filter(
            product_class_id=product.product_class_id,
            pk__in=seen,
        ).prefetch_related("options")
    }
    if set(seen) - set(attributes):
        raise _invalid("values", "Özellik bu ürünün sınıfına ait değil.")
    new_rows: list[AttributeValue] = []
    for item in items:
        new_rows.extend(_rows_for(attributes[item.attribute_id], item, product))
    AttributeValue.objects.filter(product=product, attribute_id__in=seen).delete()
    AttributeValue.objects.bulk_create(new_rows)
    search.reindex(product)
    return values_for(product)


# --- the storefront block --------------------------------------------------------


def blocks_for(product: Product) -> list[dict]:
    """The storefront "Ürün özellikleri" block: attribute name and its shown values."""
    rows = (
        AttributeValue.objects.filter(product=product)
        .select_related("attribute", "option")
        .order_by("attribute__sort_order", "attribute_id", "option__sort_order", "pk")
    )
    blocks: dict[int, dict] = {}
    for row in rows:
        block = blocks.setdefault(
            row.attribute_id,
            {"name": row.attribute.name, "values": []},
        )
        block["values"].append(row.text or row.option.label)
    return list(blocks.values())
