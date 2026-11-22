"""Owner Reports: live queries over Orders, behind one service.

Days are Europe/Istanbul days, both ends inclusive. A range is compared with the
equal number of days just before it. This module is the seam to swap if it gets slow.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.db.models import F
from django.db.models import Min
from django.db.models import Q
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncDay
from django.db.models.functions import TruncMonth
from django.db.models.functions import TruncWeek
from django.utils import timezone
from oscar.apps.catalogue.models import Product
from oscar.apps.order.models import Line
from oscar.apps.order.models import Order

from store_backend.api.errors import UnprocessableError
from store_backend.services import audit
from store_backend.services import pricing
from store_backend.services.accountant_export import safe
from store_backend.services.order_context import PAYMENT_WINDOW
from store_backend.services.order_ops import STATUS_CANCELLED
from store_backend.services.order_ops import STATUS_RETURNED
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import PaymentMethod
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from store_backend.users.models import User

MAX_DAYS = 366
CACHE_SECONDS = 60
DEFAULT_PRESET = "30d"


class Preset(StrEnum):
    TODAY = "today"
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"


class Kdv(StrEnum):
    """Which price column a Report adds up: with KDV (default) or without."""

    INCL = "incl"
    EXCL = "excl"

    @property
    def column(self) -> str:
        return f"line_price_{self.value}_tax"


class Sort(StrEnum):
    """What the Products report ranks by."""

    REVENUE = "revenue"
    UNITS = "units"


class Group(StrEnum):
    """How Sales rows are bucketed. Weeks start on Monday."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"

    def start_of(self, day: date) -> date:
        match self:
            case Group.DAY:
                return day
            case Group.WEEK:
                return day - timedelta(days=day.weekday())
            case Group.MONTH:
                return day.replace(day=1)

    def after(self, start: date) -> date:
        """The first day of the bucket following the one that starts at `start`."""
        match self:
            case Group.DAY:
                return start + timedelta(days=1)
            case Group.WEEK:
                return start + timedelta(days=7)
            case Group.MONTH:
                return (start.replace(day=28) + timedelta(days=4)).replace(day=1)

    def trunc(self, field: str):
        zone = timezone.get_current_timezone()
        match self:
            case Group.DAY:
                return TruncDay(field, tzinfo=zone)
            case Group.WEEK:
                return TruncWeek(field, tzinfo=zone)
            case Group.MONTH:
                return TruncMonth(field, tzinfo=zone)


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def before(self) -> Period:
        end = self.start - timedelta(days=1)
        return Period(end - timedelta(days=self.days - 1), end)


def today() -> date:
    return timezone.localdate()


def _preset(preset: Preset, now: date) -> Period:
    first_of_month = now.replace(day=1)
    match preset:
        case Preset.TODAY:
            return Period(now, now)
        case Preset.LAST_7_DAYS:
            return Period(now - timedelta(days=6), now)
        case Preset.LAST_30_DAYS:
            return Period(now - timedelta(days=29), now)
        case Preset.THIS_MONTH:
            return Period(first_of_month, now)
        case Preset.LAST_MONTH:
            return Period(
                (first_of_month - timedelta(days=1)).replace(day=1),
                first_of_month - timedelta(days=1),
            )


def _refuse(message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={"date_to": [message]})


def resolve(
    preset: Preset | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Period:
    """The Period asked for: a custom `start`..`end`, else a preset (default 30d)."""
    if start or end:
        if not (start and end):
            msg = "Başlangıç ve bitiş tarihi birlikte verilmelidir."
            raise _refuse(msg)
        if end < start:
            msg = "Bitiş tarihi başlangıçtan önce olamaz."
            raise _refuse(msg)
        if (end - start).days >= MAX_DAYS:
            msg = f"En fazla {MAX_DAYS} günlük aralık seçilebilir."
            raise _refuse(msg)
        return Period(start, end)
    return _preset(preset or Preset(DEFAULT_PRESET), today())


def _placed(period: Period):
    """Orders placed in `period` (shop-time days) that are not cancelled."""
    zone = timezone.get_current_timezone()
    first = datetime.combine(period.start, time.min, tzinfo=zone)
    after = datetime.combine(period.end + timedelta(days=1), time.min, tzinfo=zone)
    return Order.objects.filter(
        date_placed__gte=first,
        date_placed__lt=after,
    ).exclude(status__in=(STATUS_CANCELLED, STATUS_RETURNED))


def _sold(period: Period):
    """Orders that count as a sale: cash on delivery (a sale at placement) or a
    transfer that has been paid."""
    return (
        _placed(period)
        .filter(
            Q(sources__source_type__code=PaymentMethod.COD)
            | Q(sources__amount_debited__gt=0)
            | Q(sources__amount_refunded__gt=0),
        )
        .distinct()
    )


def _fees(period: Period, kdv: Kdv) -> dict:
    """Shipping and COD fees of the sold Orders, apart from revenue."""
    column = f"total_{kdv.value}"
    rows = (
        OrderFeeSnapshot.objects.filter(order__in=_sold(period).values("pk"))
        .values("kind")
        .annotate(total=Sum(column))
    )
    paid = {row["kind"]: row["total"] for row in rows}
    return {
        kind.value: paid.get(kind.value, pricing.ZERO) for kind in OrderFeeSnapshot.Kind
    }


def _pending(period: Period, kdv: Kdv) -> dict:
    """Unpaid bank transfers still inside their payment window, apart from sales."""
    waiting = _placed(period).filter(
        sources__source_type__code=PaymentMethod.BANK_TRANSFER,
        sources__amount_debited=0,
        sources__amount_refunded=0,
        date_placed__gt=timezone.now() - PAYMENT_WINDOW,
    )
    revenue = Line.objects.filter(order__in=waiting.values("pk")).aggregate(
        revenue=Sum(kdv.column),
    )["revenue"]
    return {
        "orders": waiting.distinct().count(),
        "revenue": revenue or pricing.ZERO,
    }


def _returned(sold, kdv: Kdv) -> tuple[int, Decimal]:
    """Units and worth of the lines Shoppers sent back and Staff received. A line's
    worth is what was paid for it (after any Voucher), pro rata to the units."""
    rows = (
        ReturnLine.objects.filter(
            request__order__in=sold.values("pk"),
            request__status__in=(ReturnStatus.RECEIVED, ReturnStatus.REFUNDED),
        )
        .values("order_line")
        .annotate(
            units=Sum("quantity"),
            paid=F(f"order_line__{kdv.column}"),
            bought=F("order_line__quantity"),
        )
    )
    units = 0
    worth = pricing.ZERO
    for row in rows:
        units += row["units"]
        worth += pricing.kurus(row["paid"] * row["units"] / row["bought"])
    return units, worth


def _returned_by_bucket(
    sold,
    kdv: Kdv,
    group: Group,
) -> dict[date, tuple[int, Decimal]]:
    """`_returned`, per bucket of the day each Order was placed."""
    rows = (
        ReturnLine.objects.filter(
            request__order__in=sold.values("pk"),
            request__status__in=(ReturnStatus.RECEIVED, ReturnStatus.REFUNDED),
        )
        .annotate(bucket=group.trunc("request__order__date_placed"))
        .values("bucket", "order_line")
        .annotate(
            units=Sum("quantity"),
            paid=F(f"order_line__{kdv.column}"),
            bought=F("order_line__quantity"),
        )
    )
    out: dict[date, tuple[int, Decimal]] = {}
    for row in rows:
        day = _day_of(row["bucket"])
        units, worth = out.get(day, (0, pricing.ZERO))
        out[day] = (
            units + row["units"],
            worth + pricing.kurus(row["paid"] * row["units"] / row["bought"]),
        )
    return out


def _totals(period: Period, kdv: Kdv) -> dict:
    sold = _sold(period)
    lines = Line.objects.filter(order__in=sold.values("pk")).aggregate(
        items=Sum("quantity"),
        revenue=Sum(kdv.column),
    )
    orders = sold.count()
    returned_units, returned_worth = _returned(sold, kdv)
    revenue = (lines["revenue"] or pricing.ZERO) - returned_worth
    return {
        "orders": orders,
        "items": (lines["items"] or 0) - returned_units,
        "revenue": revenue,
        "average_order": pricing.kurus(revenue / orders) if orders else pricing.ZERO,
    }


def _day_of(stamp) -> date:
    return timezone.localtime(stamp).date()


def _rows(period: Period, kdv: Kdv, group: Group) -> list[dict]:
    """One row per bucket of `group` across the period, empty buckets included.
    A return comes off the bucket of the day its Order was placed."""
    sold = Order.objects.filter(pk__in=_sold(period).values("pk"))
    bucket = group.trunc("date_placed")
    orders = {
        _day_of(r["bucket"]): r["n"]
        for r in sold.annotate(bucket=bucket).values("bucket").annotate(n=Count("pk"))
    }
    lines = {
        _day_of(r["bucket"]): r
        for r in Line.objects.filter(order__in=sold.values("pk"))
        .annotate(bucket=group.trunc("order__date_placed"))
        .values("bucket")
        .annotate(items=Sum("quantity"), revenue=Sum(kdv.column))
    }
    back = _returned_by_bucket(sold, kdv, group)
    rows = []
    start = group.start_of(period.start)
    while start <= period.end:
        count = orders.get(start, 0)
        line = lines.get(start, {})
        units, worth = back.get(start, (0, pricing.ZERO))
        revenue = (line.get("revenue") or pricing.ZERO) - worth
        rows.append(
            {
                "period": start,
                "orders": count,
                "items": (line.get("items") or 0) - units,
                "revenue": revenue,
                "average_order": pricing.kurus(revenue / count)
                if count
                else pricing.ZERO,
            },
        )
        start = group.after(start)
    return rows


def _sales(
    period: Period,
    kdv: Kdv = Kdv.INCL,
    group: Group = Group.DAY,
) -> dict:
    before = period.before()
    return {
        "range": {"start": period.start, "end": period.end},
        "previous": {"start": before.start, "end": before.end},
        "kdv": kdv.value,
        "group": group.value,
        "totals": _totals(period, kdv),
        "pending": _pending(period, kdv),
        "fees": _fees(period, kdv),
        "previous_totals": _totals(before, kdv),
        "rows": _rows(period, kdv, group),
    }


DELETED_PRODUCT = "Silinmiş ürün"


def _by_product(period: Period, kdv: Kdv) -> dict[int | None, dict]:
    """Units and revenue per Product (a Variant counts toward its parent), net of
    received returns. The key is None for lines whose Product no longer exists."""
    sold = Order.objects.filter(pk__in=_sold(period).values("pk"))
    item = Coalesce("product__parent_id", "product_id")
    found: dict[int | None, dict] = {
        r["item"]: {
            "units": r["units"],
            "revenue": r["revenue"],
            "label": r["label"],
        }
        for r in Line.objects.filter(order__in=sold.values("pk"))
        .annotate(item=item)
        .values("item")
        .annotate(units=Sum("quantity"), revenue=Sum(kdv.column), label=Min("title"))
    }
    back = (
        ReturnLine.objects.filter(
            request__order__in=sold.values("pk"),
            request__status__in=(ReturnStatus.RECEIVED, ReturnStatus.REFUNDED),
        )
        .annotate(
            item=Coalesce("order_line__product__parent_id", "order_line__product_id"),
        )
        .values("item", "order_line")
        .annotate(
            units=Sum("quantity"),
            paid=F(f"order_line__{kdv.column}"),
            bought=F("order_line__quantity"),
        )
    )
    for row in back:
        entry = found[row["item"]]
        entry["units"] -= row["units"]
        entry["revenue"] -= pricing.kurus(row["paid"] * row["units"] / row["bought"])
    return found


def _products(
    period: Period,
    kdv: Kdv = Kdv.INCL,
    sort: Sort = Sort.REVENUE,
) -> dict:
    """Products ranked by revenue or units, best first."""
    found = _by_product(period, kdv)
    before = period.before()
    earlier = _by_product(before, kdv)
    titles = {
        p.pk: p.title for p in Product.objects.filter(pk__in=found.keys() - {None})
    }
    rows = [
        {
            "product_id": key,
            "title": titles.get(key) or entry["label"] or DELETED_PRODUCT,
            "units": entry["units"],
            "revenue": entry["revenue"],
            "previous_units": earlier.get(key, {}).get("units", 0),
            "previous_revenue": earlier.get(key, {}).get("revenue", pricing.ZERO),
        }
        for key, entry in found.items()
    ]
    rows.sort(key=lambda r: (-r[sort.value], r["title"]))
    return {
        "range": {"start": period.start, "end": period.end},
        "previous": {"start": before.start, "end": before.end},
        "kdv": kdv.value,
        "sort": sort.value,
        "rows": rows,
    }


SALES_COLUMNS = ("period", "orders", "items", "revenue", "average_order")
PRODUCTS_COLUMNS = (
    "product_id",
    "title",
    "units",
    "revenue",
    "previous_units",
    "previous_revenue",
)


def _csv(  # noqa: PLR0913
    actor: User,
    report: str,
    *,
    period: Period,
    kdv: Kdv,
    columns: Sequence[str],
    rows: Sequence[dict],
) -> str:
    """The CSV text of one report's rows; writes the audit entry. Cells that look
    like a formula are defanged."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe(row[column]) for column in columns])
    audit.record(
        actor,
        audit.Action.EXPORT_RUN,
        target=(f"reports_{report}_csv", f"{period.start}..{period.end}"),
        summary=f"Rapor CSV ({report}): {period.start} – {period.end}",
        after={
            "from": str(period.start),
            "to": str(period.end),
            "kdv": kdv.value,
            "rows": len(rows),
        },
    )
    return out.getvalue()


@transaction.atomic
def sales_csv(actor: User, period: Period, kdv: Kdv, group: Group) -> str:
    rows = _rows(period, kdv, group)
    return _csv(
        actor,
        "sales",
        period=period,
        kdv=kdv,
        columns=SALES_COLUMNS,
        rows=rows,
    )


@transaction.atomic
def products_csv(actor: User, period: Period, kdv: Kdv, sort: Sort) -> str:
    rows = _products(period, kdv, sort)["rows"]
    return _csv(
        actor,
        "products",
        period=period,
        kdv=kdv,
        columns=PRODUCTS_COLUMNS,
        rows=rows,
    )


def _cached(key: str, build) -> dict:
    """A report's JSON, kept for a minute: the live queries are the heavy part and a
    minute of staleness is harmless. CSV downloads never come through here."""
    found = cache.get(key)
    if found is None:
        found = build()
        cache.set(key, found, CACHE_SECONDS)
    return found


def sales(
    period: Period,
    kdv: Kdv = Kdv.INCL,
    group: Group = Group.DAY,
) -> dict:
    key = f"reports:sales:{period.start}:{period.end}:{kdv}:{group}"
    return _cached(key, lambda: _sales(period, kdv, group))


def products(
    period: Period,
    kdv: Kdv = Kdv.INCL,
    sort: Sort = Sort.REVENUE,
) -> dict:
    key = f"reports:products:{period.start}:{period.end}:{kdv}:{sort}"
    return _cached(key, lambda: _products(period, kdv, sort))
