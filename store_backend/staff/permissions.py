"""The fixed, named permissions the Dashboard API checks.

Two fixed groups, Owner and Staff, are seeded by migration (not editable in the UI).
Endpoints check a *named permission*, never a group, so custom roles can come later.
Adding a permission here means a migration that adds it to the groups.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

APP_LABEL = "staff"
OWNER_GROUP = "Owner"
STAFF_GROUP = "Staff"


class Perm(StrEnum):
    # Owner and Staff
    VIEW_ORDERS = "view_orders"
    MANAGE_ORDERS = "manage_orders"  # ship, deliver, return, edit address and phone
    MARK_PAID = "mark_paid"
    VIEW_PRODUCTS = "view_products"
    MANAGE_PRODUCTS = "manage_products"
    MANAGE_PRODUCT_PHOTOS = "manage_product_photos"
    ADJUST_STOCK = "adjust_stock"
    MANAGE_CATEGORIES = "manage_categories"
    MANAGE_BRANDS = "manage_brands"
    VIEW_CUSTOMERS = "view_customers"
    MANAGE_INVOICES = "manage_invoices"
    # Owner only
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_STAFF_USERS = "manage_staff_users"
    MANAGE_COLOURS = "manage_colours"
    VIEW_COST_PRICES = "view_cost_prices"
    MANAGE_SUPPLIERS = "manage_suppliers"
    ARCHIVE_PRODUCTS = "archive_products"
    RECORD_REFUNDS = "record_refunds"
    CANCEL_PAID_ORDERS = "cancel_paid_orders"
    MANAGE_ACCOUNT_DELETIONS = "manage_account_deletions"
    EXPORT_ACCOUNTANT_CSV = "export_accountant_csv"
    VIEW_AUDIT_LOG = "view_audit_log"
    IMPORT_CATALOGUE = "import_catalogue"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()

    @property
    def permission_name(self) -> str:
        """The `app_label.codename` string Django's `has_perm` expects."""
        return f"{APP_LABEL}.{self.value}"


OWNER_ONLY: frozenset[Perm] = frozenset(
    {
        Perm.MANAGE_SETTINGS,
        Perm.MANAGE_STAFF_USERS,
        Perm.MANAGE_COLOURS,
        Perm.VIEW_COST_PRICES,
        Perm.MANAGE_SUPPLIERS,
        Perm.ARCHIVE_PRODUCTS,
        Perm.RECORD_REFUNDS,
        Perm.CANCEL_PAID_ORDERS,
        Perm.MANAGE_ACCOUNT_DELETIONS,
        Perm.EXPORT_ACCOUNTANT_CSV,
        Perm.VIEW_AUDIT_LOG,
        Perm.IMPORT_CATALOGUE,
    },
)


def omit_owner_only(user: Any, payload: Mapping[str, Any], *fields: str) -> dict:
    """Drop Owner-only `fields` (such as cost price) from `payload` unless the user is
    allowed to see cost data. Responses omit them rather than null them.
    """
    if user.has_perm(Perm.VIEW_COST_PRICES.permission_name):
        return dict(payload)
    omitted = set(fields)
    return {k: v for k, v in payload.items() if k not in omitted}
