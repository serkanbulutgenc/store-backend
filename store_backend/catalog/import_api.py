# ruff: noqa: TC002
"""Owner-only bulk catalogue import: a dry-run preview, then the same file confirmed."""

from __future__ import annotations

from ninja import File
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile

from store_backend.api.permissions import requires
from store_backend.services import catalog_import
from store_backend.staff.permissions import Perm

router = Router(tags=["catalog"])


class ImportErrorOut(Schema):
    row: int
    column: str
    message: str


class ImportRowOut(Schema):
    row: int
    action: str  # create, update or unchanged
    product: str
    colour: str
    size: str
    sku: str


class ImportCountsOut(Schema):
    products_to_create: int
    products_to_update: int
    variants_to_create: int
    variants_to_update: int
    variants_unchanged: int


class ImportPlanOut(Schema):
    counts: ImportCountsOut
    rows: list[ImportRowOut]
    errors: list[ImportErrorOut]
    new_brands: list[str]
    new_suppliers: list[str]
    new_categories: list[str]


def _out(plan: catalog_import.Plan) -> dict:
    return {
        "counts": plan.counts(),
        "rows": [
            {
                "row": ln.row,
                "action": ln.action,
                "product": ln.name,
                "colour": ln.colour.name,
                "size": ln.size,
                "sku": ln.variant.sku if ln.variant else ln.sku,
            }
            for ln in plan.lines
        ],
        "errors": [
            {"row": e.row, "column": e.column, "message": e.message}
            for e in plan.errors
        ],
        "new_brands": plan.new_brands,
        "new_suppliers": plan.new_suppliers,
        "new_categories": plan.new_categories,
    }


@router.post(
    "/catalog/import/preview",
    auth=requires(Perm.IMPORT_CATALOGUE),
    operation_id="catalog_import_preview",
    response=ImportPlanOut,
)
def preview_import(request, file: File[UploadedFile]):
    """Dry run: what the CSV would create and update, and every row error.
    Writes nothing."""
    return _out(catalog_import.preview(file.read(catalog_import.MAX_BYTES + 1)))


@router.post(
    "/catalog/import/confirm",
    auth=requires(Perm.IMPORT_CATALOGUE),
    operation_id="catalog_import_confirm",
    response=ImportPlanOut,
)
def confirm_import(request, file: File[UploadedFile]):
    """Apply the same CSV in one transaction: every row or none (422 names the
    row and column). Products are created as drafts."""
    return _out(
        catalog_import.run(
            request.auth,
            file.name or "",
            file.read(catalog_import.MAX_BYTES + 1),
        ),
    )
