# ruff: noqa: TC001, TC003
"""Owner-only Reports."""

from __future__ import annotations

import datetime as dt

from django.http import HttpResponse
from ninja import Query
from ninja import Router
from ninja import Schema

from store_backend.api.money import Money
from store_backend.api.permissions import requires_owner
from store_backend.services import reports

router = Router(tags=["reports"])


class SpanOut(Schema):
    start: dt.date
    end: dt.date


class TotalsOut(Schema):
    orders: int
    items: int
    revenue: Money
    average_order: Money


class PendingOut(Schema):
    orders: int
    revenue: Money


class FeesOut(Schema):
    shipping: Money
    cod: Money


class SalesRowOut(TotalsOut):
    period: dt.date  # the first day of the day, week (Monday) or month


class SalesReportOut(Schema):
    range: SpanOut
    previous: SpanOut
    kdv: reports.Kdv
    group: reports.Group
    totals: TotalsOut
    previous_totals: TotalsOut
    pending: PendingOut
    fees: FeesOut
    rows: list[SalesRowOut]


class ProductRowOut(Schema):
    product_id: int | None  # null for a Product that no longer exists
    title: str
    units: int
    revenue: Money
    previous_units: int
    previous_revenue: Money


class ProductsReportOut(Schema):
    range: SpanOut
    previous: SpanOut
    kdv: reports.Kdv
    sort: reports.Sort
    rows: list[ProductRowOut]


class ReasonsOut(Schema):
    """Returned units by why they came back; `unspecified` is a whole-order return."""

    wrong_size: int
    changed_mind: int
    defective: int
    wrong_item: int
    other: int
    unspecified: int


class ReturnRowOut(Schema):
    shipped: int
    returned: int
    rate: float | None  # percent of shipped; null for a row under 5 shipped units
    reasons: ReasonsOut


class ReturnProductRowOut(ReturnRowOut):
    product_id: int | None
    title: str


class ReturnSizeRowOut(ReturnRowOut):
    size: str


class ReturnsReportOut(Schema):
    range: SpanOut
    previous: SpanOut
    by_product: list[ReturnProductRowOut]
    by_size: list[ReturnSizeRowOut]


class CampaignRowOut(Schema):
    campaign_id: int
    name: str
    orders: int
    discount: Money
    revenue: Money


class VoucherRowOut(Schema):
    code: str
    orders: int
    discount: Money
    revenue: Money


class CampaignsVouchersReportOut(Schema):
    range: SpanOut
    previous: SpanOut
    kdv: reports.Kdv
    campaigns: list[CampaignRowOut]
    vouchers: list[VoucherRowOut]


class RangeParams(Schema):
    preset: reports.Preset | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None


class KdvRangeParams(RangeParams):
    kdv: reports.Kdv = reports.Kdv.INCL


class SalesParams(Schema):
    preset: reports.Preset | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    kdv: reports.Kdv = reports.Kdv.INCL
    group: reports.Group = reports.Group.DAY


class ProductsParams(Schema):
    preset: reports.Preset | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    kdv: reports.Kdv = reports.Kdv.INCL
    sort: reports.Sort = reports.Sort.REVENUE


@router.get(
    "/reports/sales",
    auth=requires_owner(),
    operation_id="reports_sales",
    response={200: SalesReportOut},
)
def sales(request, params: Query[SalesParams]):
    """Owner only. A custom `date_from`..`date_to` (at most 366 days) or a `preset`
    (default 30 days), compared with the same number of days just before it.
    Prices include KDV unless `kdv=excl`. `rows` are by `group` (day, week from
    Monday, or month)."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    return reports.sales(period, params.kdv, params.group)


@router.get(
    "/reports/products",
    auth=requires_owner(),
    operation_id="reports_products",
    response={200: ProductsReportOut},
)
def products(request, params: Query[ProductsParams]):
    """Owner only. Products ranked by `sort` (revenue or units), best first, over
    the same ranges, KDV choice and sale rules as Sales."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    return reports.products(period, params.kdv, params.sort)


def _download(text: str, name: str, period: reports.Period) -> HttpResponse:
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="rapor-{name}-{period.start}-{period.end}.csv"'
    )
    return response


@router.get(
    "/reports/sales.csv",
    auth=requires_owner(),
    operation_id="reports_sales_csv",
    response={200: str},
)
def sales_csv(request, params: Query[SalesParams]):
    """Owner only. The Sales rows as CSV, for the same query as the report.
    Audit-logged."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    text = reports.sales_csv(request.auth, period, params.kdv, params.group)
    return _download(text, "satis", period)


@router.get(
    "/reports/products.csv",
    auth=requires_owner(),
    operation_id="reports_products_csv",
    response={200: str},
)
def products_csv(request, params: Query[ProductsParams]):
    """Owner only. The Products ranking as CSV, for the same query as the report.
    Audit-logged."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    text = reports.products_csv(request.auth, period, params.kdv, params.sort)
    return _download(text, "urunler", period)


@router.get(
    "/reports/returns",
    auth=requires_owner(),
    operation_id="reports_returns",
    response={200: ReturnsReportOut},
)
def returns_report(request, params: Query[RangeParams]):
    """Owner only. Return rate by Product: returned units over units shipped, by
    order date. A row under 5 shipped units carries counts only (no rate)."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    return reports.returns(period)


@router.get(
    "/reports/returns.csv",
    auth=requires_owner(),
    operation_id="reports_returns_csv",
    response={200: str},
)
def returns_csv(request, params: Query[RangeParams]):
    """Owner only. Both Returns views as one CSV, for the same query as the
    report. Audit-logged."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    return _download(reports.returns_csv(request.auth, period), "iadeler", period)


@router.get(
    "/reports/campaigns-vouchers",
    auth=requires_owner(),
    operation_id="reports_campaigns_vouchers",
    response={200: CampaignsVouchersReportOut},
)
def campaigns_vouchers(request, params: Query[KdvRangeParams]):
    """Owner only. Per Campaign and per Voucher code: orders, the discount given
    and revenue, over the same sale rules as Sales. Prices include KDV unless
    `kdv=excl`."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    return reports.campaigns_vouchers(period, params.kdv)


@router.get(
    "/reports/campaigns-vouchers.csv",
    auth=requires_owner(),
    operation_id="reports_campaigns_vouchers_csv",
    response={200: str},
)
def campaigns_vouchers_csv(request, params: Query[KdvRangeParams]):
    """Owner only. Campaigns and Vouchers as one CSV, for the same query as the
    report. Audit-logged."""
    period = reports.resolve(params.preset, params.date_from, params.date_to)
    text = reports.campaigns_vouchers_csv(request.auth, period, params.kdv)
    return _download(text, "kampanya-kupon", period)
