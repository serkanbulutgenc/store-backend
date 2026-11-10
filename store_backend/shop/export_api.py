# ruff: noqa: TC003
"""Owner-only exports."""

from __future__ import annotations

import datetime as dt

from django.http import HttpResponse
from ninja import Router

from store_backend.api.permissions import requires
from store_backend.services import accountant_export
from store_backend.staff.permissions import Perm

router = Router(tags=["exports"])


@router.get(
    "/exports/accountant.csv",
    auth=requires(Perm.EXPORT_ACCOUNTANT_CSV),
    operation_id="exports_accountant_csv",
    response={200: str},
)
def accountant_csv(request, date_from: dt.date, date_to: dt.date):
    """Owner only. One row per invoice line for Orders placed from `date_from` to
    `date_to` (inclusive, at most 366 days). No email, phone or notes. Audit-logged."""
    text = accountant_export.export(request.auth, date_from, date_to)
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="muhasebe-{date_from}-{date_to}.csv"'
    )
    return response
