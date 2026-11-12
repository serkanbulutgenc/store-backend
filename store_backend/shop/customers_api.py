# ruff: noqa: TC001, TC003
"""Dashboard Customers: a read-only list and detail."""

from __future__ import annotations

from datetime import datetime

from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import customers
from store_backend.shop.orders_api import OrderRow
from store_backend.staff.permissions import Perm

router = Router(tags=["customers"])


class CustomerRow(Schema):
    id: int
    email: str
    name: str
    phone: str
    joined_at: datetime
    order_count: int
    anonymised: bool

    @staticmethod
    def resolve_joined_at(obj) -> datetime:
        return obj.date_joined

    @staticmethod
    def resolve_anonymised(obj) -> bool:
        return obj.anonymised_at is not None

    @staticmethod
    def resolve_email(obj) -> str:
        return "" if obj.anonymised_at else obj.email


class CustomerDetail(CustomerRow):
    marketing_consent: bool
    orders: list[OrderRow]

    @staticmethod
    def resolve_marketing_consent(obj) -> bool:
        return obj.marketing_consent_at is not None

    @staticmethod
    def resolve_orders(obj) -> list:
        return list(customers.orders_for_dashboard(obj))


@router.get(
    "/customers",
    auth=requires(Perm.VIEW_CUSTOMERS),
    operation_id="customers_list",
    response=list[CustomerRow],
)
@paginate(PageOffsetPagination)
def list_customers(request, q: str | None = None):
    """Newest first. `q` matches name and email. Anonymised accounts have no email
    or name and `anonymised` set."""
    return customers.customer_queryset(q)


@router.get(
    "/customers/{customer_id}",
    auth=requires(Perm.VIEW_CUSTOMERS),
    operation_id="customers_get",
    response=CustomerDetail,
)
def get_customer(request, customer_id: int):
    return customers.customer_detail(customer_id)
