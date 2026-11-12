# ruff: noqa: EM101, TRY003
"""Bulk catalogue import from one CSV file (Owner only).

`preview` plans the file against the current catalogue and writes nothing. `run`
plans the same file again and applies it in one transaction: any row error, or any
failure while applying, leaves the catalogue untouched. Rows with the same product
name and category become Variants of one Product; a Variant matches an existing one
by `sku` when given, else by Product + colour + size. Re-uploading a corrected file
updates price and stock instead of duplicating.

Columns (header names are case-insensitive; `,` `;` or tab separated, UTF-8):

    product_name*, category*, product_class*, colour*, size*, shelf_price_try*,
    quantity*, brand, supplier, sku, kdv_class, notes

`quantity` is the on-hand count to end up with; the difference goes through
`stock.adjust`, so it is audited. `kdv_class` is empty for the Product class default
or a percentage (0-100) overriding it. `category` is `Name` or `Parent > Child`.
`notes` is ignored. Brands, suppliers and categories that do not exist are created
on confirm; colours are never created (a colour needs a swatch), sizes must be in the
class's scheme, and Products are created as drafts.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models.functions import Lower
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import Supplier
from store_backend.catalog.models import VariantProfile
from store_backend.catalog.sizes import sizes_for
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import stock

if TYPE_CHECKING:
    from oscar.apps.partner.models import StockRecord

    from store_backend.users.models import User

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
REQUIRED = (
    "product_name",
    "category",
    "product_class",
    "colour",
    "size",
    "shelf_price_try",
    "quantity",
)
OPTIONAL = ("brand", "supplier", "sku", "kdv_class", "notes")
MAX_KDV = Decimal(100)
MAX_ERRORS_SHOWN = 20


@dataclass
class RowError:
    row: int
    column: str
    message: str


@dataclass
class Line:
    row: int
    name: str
    category: tuple[str, ...]
    pclass: ProductClass
    colour: Colour
    size: str
    price: Decimal
    quantity: int
    brand: str
    supplier: str
    sku: str
    kdv: Decimal | None
    variant: VariantProfile | None = None  # the matched existing Variant
    action: str = "create"


@dataclass
class ProductPlan:
    key: tuple
    name: str
    category: tuple[str, ...]
    pclass: ProductClass
    price: Decimal
    kdv: Decimal | None
    brand: str
    supplier: str
    product: Product | None = None  # the matched existing Product
    lines: list[Line] = field(default_factory=list)


@dataclass
class Plan:
    products: list[ProductPlan] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    new_brands: list[str] = field(default_factory=list)
    new_suppliers: list[str] = field(default_factory=list)
    new_categories: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        return sorted(
            (ln for p in self.products for ln in p.lines),
            key=lambda ln: ln.row,
        )

    def counts(self) -> dict[str, int]:
        return {
            "products_to_create": sum(1 for p in self.products if p.product is None),
            "products_to_update": sum(
                1 for p in self.products if p.product is not None
            ),
            "variants_to_create": sum(1 for ln in self.lines if ln.action == "create"),
            "variants_to_update": sum(1 for ln in self.lines if ln.action == "update"),
            "variants_unchanged": sum(
                1 for ln in self.lines if ln.action == "unchanged"
            ),
        }


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


# --- reading the file ------------------------------------------------------------


def _read(raw: bytes) -> tuple[list[tuple[int, dict[str, str]]], list[RowError]]:
    if len(raw) > MAX_BYTES:
        raise UnprocessableError(
            "Dosya çok büyük (en fazla 2 MB).",
            fields={"file": ["Dosya çok büyük (en fazla 2 MB)."]},
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        msg = "Dosya UTF-8 olarak kaydedilmeli."
        raise UnprocessableError(msg, fields={"file": [msg]}) from None
    header_line = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=header_line.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [_key(h).replace(" ", "_") for h in next(reader)]
    except StopIteration:
        msg = "Dosya boş."
        raise UnprocessableError(msg, fields={"file": [msg]}) from None
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        msg = f"Eksik sütun: {', '.join(missing)}."
        raise UnprocessableError(msg, fields={"file": [msg]})
    rows: list[tuple[int, dict[str, str]]] = []
    errors: list[RowError] = []
    try:
        for number, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue
            if len(rows) >= MAX_ROWS:
                msg = f"En fazla {MAX_ROWS} satır yüklenebilir."
                raise UnprocessableError(msg, fields={"file": [msg]})
            if len(cells) > len(header):
                errors.append(RowError(number, "", "Satırda fazladan sütun var."))
                continue
            values = dict(zip(header, (c.strip() for c in cells), strict=False))
            rows.append((number, values))
    except csv.Error:
        msg = "Dosya geçerli bir CSV değil."
        raise UnprocessableError(msg, fields={"file": [msg]}) from None
    return rows, errors


NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
MAX_AMOUNT = Decimal(1_000_000)
MAX_QUANTITY = 1_000_000


def _decimal(text: str) -> Decimal | None:
    """`199.90`, `199,90`, `1.299,90` or `1,299.90`, at most two decimals.
    Ambiguous (`1.299`) or non-numeric text (`nan`, `1e9`) is None, not a guess."""
    cleaned = text.replace("₺", "").replace("TL", "").replace(" ", "")
    if not NUMBER_RE.fullmatch(cleaned):
        return None
    marks = [c for c in cleaned if c in ".,"]
    whole, frac = cleaned, ""
    if marks:
        head, _, tail = cleaned.replace(",", ".").rpartition(".")
        thousands_only = len(marks) > 1 and len(set(marks)) == 1
        groups = re.split(r"[.,]", cleaned)
        if thousands_only and not all(len(g) == 3 for g in groups[1:]):  # noqa: PLR2004
            return None
        if len(tail) == 0 or (len(tail) == 3 and not thousands_only):  # noqa: PLR2004
            return None
        if not thousands_only:
            whole, frac = head, tail
    value = Decimal(f"{whole.replace('.', '').replace(',', '')}.{frac or '0'}")
    return value.quantize(Decimal("0.01")) if value <= MAX_AMOUNT else None


# --- lookups ---------------------------------------------------------------------


@dataclass
class _Catalogue:
    classes: dict[str, ProductClass]
    colours: dict[str, Colour]
    brands: set[str]
    suppliers: set[str]
    roots: dict[str, Category]
    children: dict[tuple[int, str], Category]

    @classmethod
    def load(cls) -> _Catalogue:
        categories = list(Category.objects.all().order_by("path"))
        roots = {_key(c.name): c for c in categories if c.depth == 1}
        by_path = {c.path: c for c in categories}
        children = {
            (by_path[c.path[: -c.steplen]].pk, _key(c.name)): c
            for c in categories
            if c.depth > 1
        }
        return cls(
            classes={
                _key(pc.name): pc
                for pc in ProductClass.objects.select_related("profile")
            },
            colours={_key(c.name): c for c in Colour.objects.all()},
            brands={_key(b.name) for b in Brand.objects.all()},
            suppliers={_key(s.name) for s in Supplier.objects.all()},
            roots=roots,
            children=children,
        )

    def category(self, path: tuple[str, ...]) -> Category | None:
        node = self.roots.get(_key(path[0]))
        if node is not None and len(path) > 1:
            node = self.children.get((node.pk, _key(path[1])))
        return node


def _category_path(text: str) -> tuple[str, ...] | None:
    parts = tuple(" ".join(p.split()) for p in text.split(">"))
    if not 1 <= len(parts) <= catalog.MAX_CATEGORY_DEPTH or not all(parts):
        return None
    return parts


# --- planning --------------------------------------------------------------------


def _parse_line(  # noqa: C901
    number: int,
    values: dict[str, str],
    cat: _Catalogue,
    errors: list[RowError],
) -> Line | None:
    before = len(errors)

    def bad(column: str, message: str) -> None:
        errors.append(RowError(number, column, message))

    def get(column: str) -> str:
        return " ".join(values.get(column, "").split())

    name = get("product_name")
    if not name:
        bad("product_name", "Ürün adı girin.")
    path = _category_path(get("category"))
    if path is None:
        bad("category", "Kategori `Ad` veya `Üst > Alt` biçiminde olmalı.")
    pclass = cat.classes.get(_key(get("product_class")))
    if pclass is None:
        bad("product_class", "Bilinmeyen ürün sınıfı.")
    colour = cat.colours.get(_key(get("colour")))
    if colour is None:
        bad("colour", "Bilinmeyen renk; önce Renkler ekranından ekleyin.")
    elif not colour.is_active:
        bad("colour", "Bu renk kullanımda değil.")
    size = get("size")
    if pclass is not None and size not in sizes_for(pclass.profile.size_scheme):
        bad("size", "Bu ürün sınıfı için geçersiz beden.")
    price = _decimal(get("shelf_price_try"))
    if price is None or price <= 0:
        bad("shelf_price_try", "Geçerli bir fiyat girin (en fazla 2 ondalık).")
    quantity_text = get("quantity")
    quantity = (
        int(quantity_text)
        if quantity_text.isascii()
        and quantity_text.isdecimal()
        and len(quantity_text) <= len(str(MAX_QUANTITY))
        else None
    )
    if quantity is None or quantity > MAX_QUANTITY:
        quantity = None
        bad("quantity", "Adet 0 veya daha büyük bir tam sayı olmalı.")
    kdv = None
    if get("kdv_class"):
        kdv = _decimal(get("kdv_class").rstrip("%"))
        if kdv is None or not 0 <= kdv <= MAX_KDV:
            bad("kdv_class", "KDV oranı boş veya 0 ile 100 arasında olmalı.")
    if len(errors) > before:
        return None
    return Line(
        row=number,
        name=name,
        category=path,  # type: ignore[arg-type]
        pclass=pclass,
        colour=colour,  # type: ignore[arg-type]
        size=size,
        price=price,  # type: ignore[arg-type]
        quantity=quantity,  # type: ignore[arg-type]
        brand=get("brand"),
        supplier=get("supplier"),
        sku=get("sku"),
        kdv=kdv,
    )


def _group(lines: list[Line], errors: list[RowError]) -> list[ProductPlan]:
    groups: dict[tuple, ProductPlan] = {}
    for ln in lines:
        key = (_key(ln.name), tuple(_key(p) for p in ln.category))
        plan = groups.get(key)
        if plan is None:
            plan = groups[key] = ProductPlan(
                key=key,
                name=ln.name,
                category=ln.category,
                pclass=ln.pclass,
                price=ln.price,
                kdv=ln.kdv,
                brand=ln.brand,
                supplier=ln.supplier,
            )
        elif ln.pclass.pk != plan.pclass.pk:
            errors.append(
                RowError(
                    ln.row,
                    "product_class",
                    "Aynı ürünün satırlarında ürün sınıfı aynı olmalı.",
                ),
            )
            continue
        plan.brand = plan.brand or ln.brand
        plan.supplier = plan.supplier or ln.supplier
        plan.lines.append(ln)
    for plan in groups.values():
        # The Product price is the most common line price (lowest on a tie), so row
        # order does not decide it.
        prices = [ln.price for ln in plan.lines]
        plan.price = min(prices, key=lambda x: (-prices.count(x), x))
    return list(groups.values())


def _match_products(
    plans: list[ProductPlan],
    cat: _Catalogue,
    errors: list[RowError],
) -> dict[int, dict[tuple[int, str], VariantProfile]]:
    """Attach existing Products and Variants; returns variants by product pk."""
    names = {_key(p.name) for p in plans}
    existing: dict[tuple[str, int], Product] = {}
    candidates = (
        Product.objects.filter(structure=Product.PARENT)
        .annotate(lowered=Lower("title"))
        .filter(lowered__in=[n.lower() for n in names])
        .select_related("profile")
        .prefetch_related("categories")
    )
    for product in candidates:
        if _key(product.title) not in names:
            continue
        for category in product.categories.all():
            existing.setdefault((_key(product.title), category.pk), product)
    by_product: dict[int, dict[tuple[int, str], VariantProfile]] = {}
    for plan in plans:
        category = cat.category(plan.category)
        if category is None:
            continue
        product = existing.get((_key(plan.name), category.pk))
        if product is None:
            continue
        if product.product_class_id != plan.pclass.pk:
            errors.extend(
                RowError(
                    ln.row,
                    "product_class",
                    "Mevcut ürünün sınıfı değiştirilemez.",
                )
                for ln in plan.lines
            )
            continue
        plan.product = product
        by_product[product.pk] = {}
    if by_product:
        for vp in VariantProfile.objects.filter(parent_id__in=by_product):
            by_product[vp.parent_id][vp.colour_id, vp.size] = vp
    return by_product


def _match_variants(  # noqa: C901
    plans: list[ProductPlan],
    by_product: dict[int, dict[tuple[int, str], VariantProfile]],
    errors: list[RowError],
) -> None:
    skus = {ln.sku for p in plans for ln in p.lines if ln.sku}
    by_sku = {vp.sku: vp for vp in VariantProfile.objects.filter(sku__in=skus)}
    seen_sku: set[str] = set()
    for plan in plans:
        known = by_product.get(plan.product.pk, {}) if plan.product else {}
        seen: set[tuple[int, str]] = set()
        for ln in plan.lines:
            slot = (ln.colour.pk, ln.size)
            if slot in seen:
                errors.append(
                    RowError(ln.row, "size", "Bu renk ve beden dosyada tekrar ediyor."),
                )
                continue
            seen.add(slot)
            if ln.sku:
                if ln.sku in seen_sku:
                    errors.append(
                        RowError(ln.row, "sku", "Bu SKU dosyada tekrar ediyor."),
                    )
                    continue
                seen_sku.add(ln.sku)
            match = known.get(slot)
            by_given = by_sku.get(ln.sku) if ln.sku else None
            if by_given is not None:
                same = (
                    plan.product is not None and by_given.parent_id == plan.product.pk
                )
                if not same or (by_given.colour_id, by_given.size) != slot:
                    errors.append(
                        RowError(
                            ln.row,
                            "sku",
                            "Bu SKU başka bir ürün, renk veya bedene ait.",
                        ),
                    )
                    continue
                match = by_given
            elif match is not None and ln.sku and match.sku != ln.sku:
                errors.append(
                    RowError(
                        ln.row,
                        "sku",
                        f"Bu ürün, renk ve beden zaten {match.sku} SKU'su ile var.",
                    ),
                )
                continue
            if match is not None:
                ln.variant = match
        if plan.product is not None and set(known) - seen:
            # A partial file must not reprice the variants it leaves out.
            plan.price = plan.product.profile.shelf_price


def _classify(
    plans: list[ProductPlan],
    errors: list[RowError],
) -> None:
    """Decide create / update / unchanged per line and check price rules."""
    variant_ids = [ln.variant.product_id for p in plans for ln in p.lines if ln.variant]
    records: dict[int, StockRecord] = stock.records_for(variant_ids)
    for plan in plans:
        profile = plan.product.profile if plan.product else None
        for ln in plan.lines:
            if (
                profile is not None
                and profile.shelf_price != plan.price
                and profile.old_price is not None
                and profile.old_price <= plan.price
            ):
                errors.append(
                    RowError(
                        ln.row,
                        "shelf_price_try",
                        "Eski fiyat satış fiyatından yüksek olmalı; önce eski fiyatı "
                        "düzenleyin.",
                    ),
                )
            if ln.variant is None:
                ln.action = "create"
                continue
            vp = ln.variant
            record = records.get(vp.product_id)
            wanted = None if ln.price == plan.price else ln.price
            changed = (
                wanted != vp.price_override
                or (record is not None and record.num_in_stock != ln.quantity)
                or (profile is not None and profile.shelf_price != plan.price)
            )
            ln.action = "update" if changed else "unchanged"


def build_plan(raw: bytes) -> Plan:
    """Check the file against the catalogue; writes nothing."""
    rows, errors = _read(raw)
    cat = _Catalogue.load()
    lines = [
        ln
        for number, values in rows
        if (ln := _parse_line(number, values, cat, errors)) is not None
    ]
    plans = _group(lines, errors)
    by_product = _match_products(plans, cat, errors)
    _match_variants(plans, by_product, errors)
    _classify(plans, errors)
    plan = Plan(products=plans, errors=sorted(errors, key=lambda e: e.row))
    _collect_new(plan, cat)
    return plan


def _collect_new(plan: Plan, cat: _Catalogue) -> None:
    brands: dict[str, str] = {}
    suppliers: dict[str, str] = {}
    categories: dict[tuple[str, ...], str] = {}
    for p in plan.products:
        if p.brand and _key(p.brand) not in cat.brands:
            brands.setdefault(_key(p.brand), p.brand)
        if p.supplier and _key(p.supplier) not in cat.suppliers:
            suppliers.setdefault(_key(p.supplier), p.supplier)
        for depth in range(1, len(p.category) + 1):
            path = p.category[:depth]
            if cat.category(path) is None:
                categories.setdefault(
                    tuple(_key(x) for x in path),
                    " > ".join(path),
                )
    plan.new_brands = list(brands.values())
    plan.new_suppliers = list(suppliers.values())
    plan.new_categories = list(categories.values())


# --- applying --------------------------------------------------------------------


def _supplier_code(name: str) -> str:
    base = re.sub(r"[^A-Z0-9]", "", catalog.slugify_tr(name).upper())[:6] or "TED"
    code, n = base, 1
    while Supplier.objects.filter(code__iexact=code).exists():
        n += 1
        code = f"{base}{n}"
    return code


def _ensure_parents(plan: Plan) -> tuple[dict, dict, dict]:
    brands = {_key(b.name): b for b in Brand.objects.all()}
    suppliers = {_key(s.name): s for s in Supplier.objects.all()}
    cat = _Catalogue.load()
    for name in plan.new_brands:
        brands[_key(name)] = catalog.create_brand(name)
    for name in plan.new_suppliers:
        suppliers[_key(name)] = catalog.create_supplier(name, _supplier_code(name))
    categories: dict[tuple[str, ...], Category] = {}
    for p in plan.products:
        parent = None
        for depth in range(1, len(p.category) + 1):
            path = p.category[:depth]
            marker = tuple(_key(x) for x in path)
            node = categories.get(marker) or cat.category(path)
            if node is None:
                node = catalog.create_category(path[-1], parent=parent)
            categories[marker] = node
            parent = node
    return brands, suppliers, categories


def _apply_product(
    actor: User,
    p: ProductPlan,
    refs: tuple[dict, dict, dict],
    reason: str,
) -> None:
    brands, suppliers, categories = refs
    if p.product is None:
        p.product = catalog.create_product(
            title=p.name,
            product_class=p.pclass,
            category=categories[tuple(_key(x) for x in p.category)],
            shelf_price=p.price,
            brand=brands.get(_key(p.brand)),
            supplier=suppliers.get(_key(p.supplier)),
            kdv_rate=p.kdv,
        )
    else:
        changes: dict[str, object] = {}
        if p.product.profile.shelf_price != p.price:
            changes["shelf_price"] = p.price
        if p.kdv is not None and p.product.profile.kdv_rate != p.kdv:
            changes["kdv_rate"] = p.kdv
        if changes:
            catalog.update_product(actor, p.product, **changes)
    for ln in p.lines:
        override = None if ln.price == p.price else ln.price
        if ln.variant is None:
            variant = catalog.create_variant(
                p.product,
                colour=ln.colour,
                size=ln.size,
                sku=ln.sku or None,
                price_override=override,
            )
            current = 0
        else:
            variant = ln.variant.product
            if ln.variant.price_override != override:
                catalog.update_variant(actor, variant, price_override=override)
            record = stock.record_for(variant)
            current = record.num_in_stock if record else 0
        if ln.quantity != current:
            stock.adjust(actor, variant, delta=ln.quantity - current, reason=reason)


def _fail(errors: list[RowError]) -> UnprocessableError:
    lines = [f"Satır {e.row}, {e.column or 'satır'}: {e.message}" for e in errors]
    shown = lines[:MAX_ERRORS_SHOWN]
    if len(lines) > len(shown):
        shown.append(f"… ve {len(lines) - len(shown)} hata daha.")
    msg = "Dosyada hatalar var; hiçbir şey kaydedilmedi."
    return UnprocessableError(msg, fields={"file": shown})


def preview(raw: bytes) -> Plan:
    return build_plan(raw)


@transaction.atomic
def run(actor: User, filename: str, raw: bytes) -> Plan:
    """Apply the file, all rows or none; one audit entry for the run."""
    plan = build_plan(raw)
    if plan.errors:
        raise _fail(plan.errors)
    name = filename.strip()[:100] or "dosya.csv"
    refs = _ensure_parents(plan)
    for p in plan.products:
        _apply_product(actor, p, refs, f"İçe aktarma: {name}")
    counts = plan.counts()
    audit.record(
        actor,
        audit.Action.CATALOGUE_IMPORT,
        target=("catalogue_import", name),
        summary=(
            f"Katalog içe aktarma: {name} "
            f"({counts['variants_to_create']} yeni, "
            f"{counts['variants_to_update']} güncellenen varyant)"
        ),
        after={"file": name, **counts},
    )
    return plan
