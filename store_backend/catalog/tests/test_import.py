"""Bulk catalogue import: planning, applying, re-uploading and the endpoints."""

from __future__ import annotations

import time
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product

from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import Brand
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import Supplier
from store_backend.catalog.models import VariantProfile
from store_backend.services import audit
from store_backend.services import catalog
from store_backend.services import catalog_import
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

HEADER = (
    "product_name,category,brand,supplier,product_class,colour,size,sku,"
    "shelf_price_try,kdv_class,quantity,notes\n"
)
ROWS = (
    "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Siyah,M,,199.90,,5,\n"
    "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Siyah,L,,199.90,,3,\n"
    "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Kırmızı,M,,219.90,,2,\n"
)
BASE = "/api/admin/catalog/import"


def csv_bytes(rows: str = ROWS, header: str = HEADER) -> bytes:
    return (header + rows).encode()


@pytest.fixture
def owner():
    return staff_member("Owner")


@pytest.fixture
def colours():
    catalog.create_colour("Siyah", "#000000")
    catalog.create_colour("Kırmızı", "#FF0000")


def variants():
    return VariantProfile.objects.count()


def stock_of(sku: str) -> int:
    vp = VariantProfile.objects.get(sku=sku)
    return vp.product.stockrecords.get().num_in_stock


# --- preview writes nothing ------------------------------------------------------


def test_preview_reports_the_plan_and_writes_nothing(colours):
    plan = catalog_import.preview(csv_bytes())
    assert plan.errors == []
    assert plan.counts() == {
        "products_to_create": 1,
        "products_to_update": 0,
        "variants_to_create": 3,
        "variants_to_update": 0,
        "variants_unchanged": 0,
    }
    assert plan.new_brands == ["Moda"]
    assert plan.new_suppliers == ["Ted A"]
    assert plan.new_categories == ["Kadın", "Kadın > Elbise"]
    assert Product.objects.count() == 0
    assert Brand.objects.count() == 0
    assert Category.objects.count() == 0


# --- confirm creates -------------------------------------------------------------


def test_confirm_creates_one_draft_product_with_variants_and_audited_stock(
    owner,
    colours,
):
    catalog_import.run(owner, "stok.csv", csv_bytes())
    product = Product.objects.get(structure=Product.PARENT)
    assert product.profile.status == ProductStatus.DRAFT
    assert product.profile.shelf_price == Decimal("199.90")
    assert product.children.count() == 3  # noqa: PLR2004
    red = VariantProfile.objects.get(colour__name="Kırmızı")
    assert red.price_override == Decimal("219.90")
    assert (
        VariantProfile.objects.get(colour__name="Siyah", size="M").price_override
        is None
    )
    assert stock_of(red.sku) == 2  # noqa: PLR2004
    assert Brand.objects.filter(name="Moda").exists()
    assert Supplier.objects.filter(name="Ted A").exists()
    assert (
        AuditEntry.objects.filter(action=audit.Action.STOCK_ADJUSTMENT).count() == 3  # noqa: PLR2004
    )
    entry = AuditEntry.objects.get(action=audit.Action.CATALOGUE_IMPORT)
    assert entry.actor == owner
    assert "stok.csv" in entry.summary
    assert (entry.after or {})["variants_to_create"] == 3  # noqa: PLR2004


# --- one bad row changes nothing -------------------------------------------------


def test_one_bad_row_changes_nothing_and_names_row_and_column(owner, colours):
    bad = ROWS + "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Mor,M,,99.90,,1,\n"
    with pytest.raises(UnprocessableError) as caught:
        catalog_import.run(owner, "stok.csv", csv_bytes(bad))
    assert "Satır 5, colour" in caught.value.fields["file"][0]
    assert Product.objects.count() == 0
    assert Brand.objects.count() == 0
    assert AuditEntry.objects.filter(action=audit.Action.CATALOGUE_IMPORT).count() == 0


@pytest.mark.parametrize(
    ("row", "column"),
    [
        ("E,K,,,Giyim,Siyah,M,,abc,,1,\n", "shelf_price_try"),
        ("E,K,,,Giyim,Siyah,M,,-5,,1,\n", "shelf_price_try"),
        ("E,K,,,Giyim,Siyah,38,,10,,1,\n", "size"),
        ("E,K,,,Bilinmez,Siyah,M,,10,,1,\n", "product_class"),
        ("E,K,,,Giyim,Siyah,M,,10,,-1,\n", "quantity"),
        ("E,K,,,Giyim,Siyah,M,,10,150,1,\n", "kdv_class"),
        (",K,,,Giyim,Siyah,M,,10,,1,\n", "product_name"),
        ("E,A > B > C > D > E > F,,,Giyim,Siyah,M,,10,,1,\n", "category"),
    ],
)
def test_row_errors_name_the_column(colours, row, column):
    plan = catalog_import.preview(csv_bytes(row))
    assert [(e.row, e.column) for e in plan.errors] == [(2, column)]


def test_duplicate_variant_rows_in_the_file_are_errors(colours):
    plan = catalog_import.preview(csv_bytes(ROWS + ROWS.splitlines()[0] + "\n"))
    assert [(e.row, e.column) for e in plan.errors] == [(5, "size")]


def test_missing_required_column_rejects_the_file(colours):
    with pytest.raises(UnprocessableError):
        catalog_import.preview(b"product_name,category\nA,B\n")


# --- re-upload updates instead of duplicating --------------------------------------


def test_reuploading_a_corrected_file_updates_price_and_stock(owner, colours):
    catalog_import.run(owner, "a.csv", csv_bytes())
    corrected = (
        "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Siyah,M,,249.90,,9,\n"
        "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Siyah,L,,249.90,,3,\n"
        "Elbise,Kadın > Elbise,Moda,Ted A,Giyim,Kırmızı,M,,249.90,,2,\n"
    )
    plan = catalog_import.preview(csv_bytes(corrected))
    assert plan.counts()["variants_to_create"] == 0
    assert plan.counts()["products_to_update"] == 1
    catalog_import.run(owner, "b.csv", csv_bytes(corrected))
    assert Product.objects.filter(structure=Product.PARENT).count() == 1
    assert variants() == 3  # noqa: PLR2004
    product = Product.objects.get(structure=Product.PARENT)
    assert product.profile.shelf_price == Decimal("249.90")
    assert VariantProfile.objects.filter(price_override__isnull=False).count() == 0
    black_m = VariantProfile.objects.get(colour__name="Siyah", size="M")
    assert stock_of(black_m.sku) == 9  # noqa: PLR2004


def test_same_file_twice_changes_nothing_the_second_time(owner, colours):
    catalog_import.run(owner, "a.csv", csv_bytes())
    plan = catalog_import.preview(csv_bytes())
    assert plan.counts()["variants_unchanged"] == 3  # noqa: PLR2004
    assert plan.counts()["variants_to_update"] == 0


def test_sku_matches_an_existing_variant_and_a_foreign_sku_is_an_error(
    owner,
    colours,
):
    catalog_import.run(owner, "a.csv", csv_bytes())
    sku = VariantProfile.objects.get(colour__name="Kırmızı").sku
    again = f"Elbise,Kadın > Elbise,,,Giyim,Kırmızı,M,{sku},219.90,,7,\n"
    plan = catalog_import.preview(csv_bytes(again))
    assert plan.errors == []
    assert plan.lines[0].action == "update"
    wrong = f"Elbise,Kadın > Elbise,,,Giyim,Siyah,XL,{sku},10,,1,\n"
    assert [e.column for e in catalog_import.preview(csv_bytes(wrong)).errors] == [
        "sku",
    ]


def test_same_name_in_another_category_is_a_different_product(owner, colours):
    catalog_import.run(owner, "a.csv", csv_bytes())
    other = "Elbise,Erkek,,,Giyim,Siyah,M,,50,,1,\n"
    catalog_import.run(owner, "b.csv", csv_bytes(other))
    assert Product.objects.filter(structure=Product.PARENT).count() == 2  # noqa: PLR2004


def test_semicolon_files_with_comma_prices_and_bom_are_read(owner, colours):
    text = HEADER.replace(",", ";") + "Tişört;Giyim;;;Giyim;Siyah;M;;1.299,90;;4;\n"
    plan = catalog_import.preview(b"\xef\xbb\xbf" + text.encode())
    assert plan.errors == []
    assert plan.products[0].price == Decimal("1299.90")


# --- endpoints -------------------------------------------------------------------


def upload(api, path, data: bytes):
    return api.client.post(
        path,
        {"file": SimpleUploadedFile("stok.csv", data, content_type="text/csv")},
        headers=api.headers,
    )


def test_staff_cannot_call_either_endpoint(api_for, colours):
    api = api_for(staff_member("Staff"), enrol=True)
    for path in ("preview", "confirm"):
        assert upload(api, f"{BASE}/{path}", csv_bytes()).status_code == (
            HTTPStatus.FORBIDDEN
        )
    assert Product.objects.count() == 0


def test_owner_previews_then_confirms_over_http(api_for, owner, colours):
    api = api_for(owner, enrol=True)
    preview = upload(api, f"{BASE}/preview", csv_bytes())
    assert preview.status_code == HTTPStatus.OK
    assert preview.json()["counts"]["variants_to_create"] == 3  # noqa: PLR2004
    assert Product.objects.count() == 0
    confirm = upload(api, f"{BASE}/confirm", csv_bytes())
    assert confirm.status_code == HTTPStatus.OK
    assert variants() == 3  # noqa: PLR2004


def test_confirm_with_a_bad_row_is_422_and_writes_nothing(api_for, owner, colours):
    api = api_for(owner, enrol=True)
    bad = upload(api, f"{BASE}/confirm", csv_bytes(ROWS + "x,y,,,Giyim,Mor,M,,1,,1,\n"))
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert Product.objects.count() == 0


def test_a_thousand_rows_preview_in_a_few_seconds(colours):
    rows = "".join(
        f"Ürün {n},Kadın,Moda,,Giyim,{colour},{size},,{100 + n},,{n % 7}\n"
        for n in range(250)
        for colour in ("Siyah", "Kırmızı")
        for size in ("S", "M")
    )
    started = time.monotonic()
    plan = catalog_import.preview(csv_bytes(rows))
    assert plan.errors == []
    assert plan.counts()["variants_to_create"] == 1000  # noqa: PLR2004
    assert time.monotonic() - started < 5  # noqa: PLR2004


@pytest.mark.parametrize(
    "price",
    ["nan", "inf", "1e999999", "1.299", "0.004", "99999999999"],
)
def test_odd_prices_are_row_errors_not_crashes(colours, price):
    plan = catalog_import.preview(csv_bytes(f"E,K,,,Giyim,Siyah,M,,{price},,1,\n"))
    assert [e.column for e in plan.errors] == ["shelf_price_try"]


@pytest.mark.parametrize("quantity", ["9" * 5000, "nan", "1000001", "１２"])
def test_odd_quantities_are_row_errors_not_crashes(colours, quantity):
    plan = catalog_import.preview(csv_bytes(f"E,K,,,Giyim,Siyah,M,,10,,{quantity},\n"))
    assert [e.column for e in plan.errors] == ["quantity"]


def test_kdv_nan_is_a_row_error(colours):
    plan = catalog_import.preview(csv_bytes("E,K,,,Giyim,Siyah,M,,10,nan,1,\n"))
    assert [e.column for e in plan.errors] == ["kdv_class"]


def test_unparseable_csv_is_422_not_500(colours):
    with pytest.raises(UnprocessableError):
        catalog_import.preview(csv_bytes('E,"' + "x" * 200_000 + '",,,,,,,,,,\n'))


def test_a_partial_file_does_not_reprice_variants_it_leaves_out(owner, colours):
    catalog_import.run(owner, "a.csv", csv_bytes())  # Elbise 199.90, red 219.90
    only_red = "Elbise,Kadın > Elbise,,,Giyim,Kırmızı,M,,229.90,,2,\n"
    catalog_import.run(owner, "b.csv", csv_bytes(only_red))
    product = Product.objects.get(structure=Product.PARENT)
    assert product.profile.shelf_price == Decimal("199.90")
    red = VariantProfile.objects.get(colour__name="Kırmızı")
    assert red.price_override == Decimal("229.90")


def test_product_price_does_not_depend_on_row_order(colours):
    rows = ROWS.splitlines()
    flipped = "\n".join(reversed(rows)) + "\n"
    first = catalog_import.preview(csv_bytes(ROWS)).products[0].price
    assert catalog_import.preview(csv_bytes(flipped)).products[0].price == first


DEEP = "Kadın > Giyim > Elbise > Midi > Yazlık"


def deep_row(path: str = DEEP, size: str = "M") -> str:
    return f"Elbise,{path},Moda,,Giyim,Siyah,{size},,199.90,,5,\n"


def category_names() -> list[str]:
    return [c.name for c in Category.objects.order_by("path")]


def test_a_five_part_category_path_creates_the_chain_once(api_for, owner, colours):
    api = api_for(owner, enrol=True)

    first = upload(api, f"{BASE}/confirm", csv_bytes(deep_row()))
    again = upload(api, f"{BASE}/confirm", csv_bytes(deep_row()))

    assert first.status_code == HTTPStatus.OK
    assert first.json()["new_categories"] == [
        "Kadın",
        "Kadın > Giyim",
        "Kadın > Giyim > Elbise",
        "Kadın > Giyim > Elbise > Midi",
        DEEP,
    ]
    assert again.status_code == HTTPStatus.OK
    assert again.json()["new_categories"] == []
    assert category_names() == ["Kadın", "Giyim", "Elbise", "Midi", "Yazlık"]
    assert [c.depth for c in Category.objects.order_by("path")] == [1, 2, 3, 4, 5]
    assert Product.objects.filter(structure=Product.PARENT).count() == 1


def test_only_the_missing_ancestors_are_created(api_for, owner, colours):
    api = api_for(owner, enrol=True)
    upload(api, f"{BASE}/confirm", csv_bytes(deep_row("Kadın > Giyim")))

    response = upload(api, f"{BASE}/confirm", csv_bytes(deep_row(DEEP, "L")))

    assert response.json()["new_categories"] == [
        "Kadın > Giyim > Elbise",
        "Kadın > Giyim > Elbise > Midi",
        DEEP,
    ]
    assert category_names().count("Kadın") == 1
    assert category_names().count("Giyim") == 1


def test_a_six_part_category_path_is_a_row_error_on_category(api_for, owner, colours):
    api = api_for(owner, enrol=True)

    response = upload(api, f"{BASE}/preview", csv_bytes(deep_row(DEEP + " > Altı")))

    assert response.status_code == HTTPStatus.OK
    assert [(e["row"], e["column"]) for e in response.json()["errors"]] == [
        (2, "category"),
    ]
    assert Category.objects.count() == 0


# --- description column ----------------------------------------------------------

DESCRIPTION_HEADER = HEADER.rstrip("\n") + ",description\n"


def with_description(*descriptions: str) -> bytes:
    """The standard three rows, each ending in the given (CSV-quoted) description."""
    rows = "".join(
        line + "," + text + "\n"
        for line, text in zip(ROWS.splitlines(), descriptions, strict=True)
    )
    return csv_bytes(rows, DESCRIPTION_HEADER)


def parent_product() -> Product:
    return Product.objects.get(structure=Product.PARENT)


def test_import_sets_a_new_products_description_keeping_its_line_breaks(
    owner,
    colours,
):
    markdown = '"**Pamuklu**\n\n- yıkanabilir"'
    catalog_import.run(owner, "a.csv", with_description(markdown, "", ""))

    assert parent_product().description == "**Pamuklu**\n\n- yıkanabilir"


def test_reimport_updates_or_keeps_the_description_and_blank_never_clears(
    owner,
    colours,
):
    catalog_import.run(owner, "a.csv", with_description("Eski", "", ""))
    assert parent_product().description == "Eski"

    catalog_import.run(owner, "b.csv", with_description("", "Yeni **metin**", ""))
    assert parent_product().description == "Yeni **metin**"  # first non-blank row

    catalog_import.run(owner, "c.csv", csv_bytes())  # no description column
    assert parent_product().description == "Yeni **metin**"

    catalog_import.run(owner, "d.csv", with_description("", "", ""))  # blank cells
    assert parent_product().description == "Yeni **metin**"


def test_rows_of_one_product_with_different_descriptions_are_a_row_error(
    owner,
    colours,
):
    with pytest.raises(UnprocessableError) as caught:
        catalog_import.run(owner, "a.csv", with_description("Bir", "İki", ""))

    assert "Satır 3, description" in caught.value.fields["file"][0]
    assert Product.objects.count() == 0


def test_an_over_long_description_is_a_row_error_and_writes_nothing(owner, colours):
    too_long = "a" * (catalog.DESCRIPTION_MAX + 1)
    with pytest.raises(UnprocessableError) as caught:
        catalog_import.run(owner, "a.csv", with_description(too_long, "", ""))

    assert "Satır 2, description" in caught.value.fields["file"][0]
    assert Product.objects.count() == 0
    just_fits = "a" * catalog.DESCRIPTION_MAX
    catalog_import.run(owner, "b.csv", with_description(just_fits, "", ""))
    assert parent_product().description == just_fits


# --- fit_note column -------------------------------------------------------------


def with_fit_note(*notes: str, column: str = "fit_note") -> bytes:
    """The standard three rows, each ending in the given fit note cell."""
    rows = "".join(
        line + "," + text + "\n"
        for line, text in zip(ROWS.splitlines(), notes, strict=True)
    )
    return csv_bytes(rows, HEADER.rstrip("\n") + f",{column}\n")


def test_import_sets_a_products_fit_note(owner, colours):
    catalog_import.run(owner, "a.csv", with_fit_note("Dar kalıp", "", ""))

    assert parent_product().profile.fit_note == "Dar kalıp"


def test_reimport_updates_the_fit_note_and_empty_never_clears_it(owner, colours):
    catalog_import.run(owner, "a.csv", with_fit_note("Dar kalıp", "", ""))

    catalog_import.run(owner, "b.csv", with_fit_note("", "Bir beden büyük alın", ""))
    assert parent_product().profile.fit_note == "Bir beden büyük alın"

    catalog_import.run(owner, "c.csv", csv_bytes())  # no fit_note column
    assert parent_product().profile.fit_note == "Bir beden büyük alın"

    catalog_import.run(owner, "d.csv", with_fit_note("", "", ""))  # empty cells
    assert parent_product().profile.fit_note == "Bir beden büyük alın"


def test_rows_of_one_product_with_different_fit_notes_are_a_row_error(owner, colours):
    with pytest.raises(UnprocessableError) as caught:
        catalog_import.run(owner, "a.csv", with_fit_note("Dar", "Bol", ""))

    assert "Satır 3, fit_note" in caught.value.fields["file"][0]


def test_a_fit_note_over_the_length_cap_is_a_row_error(owner, colours):
    with pytest.raises(UnprocessableError) as caught:
        catalog_import.run(owner, "a.csv", with_fit_note("x" * 121, "", ""))

    assert "Satır 2, fit_note" in caught.value.fields["file"][0]


def test_a_size_guide_column_is_ignored_because_guides_are_not_importable(
    owner,
    colours,
):
    catalog_import.run(
        owner,
        "a.csv",
        with_fit_note("| Beden |", "", "", column="size_guide"),
    )

    product = parent_product()
    assert product.profile.size_guide == ""
    assert product.profile.fit_note == ""
    assert product.product_class.profile.size_guide == ""
