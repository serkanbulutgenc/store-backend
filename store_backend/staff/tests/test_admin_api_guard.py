"""Guard tests for the Dashboard API's Owner/Staff boundary (docs/adr/0001).

They walk every operation of `admin_api`, so a new endpoint cannot ship without a
declared permission, and cannot become Owner-only or Staff-open without this file
changing in the same diff.
"""

from __future__ import annotations

import re

import pytest

from store_backend.api.admin import admin_api
from store_backend.api.permissions import RequiresPermission
from store_backend.staff.permissions import OWNER_ONLY
from store_backend.staff.permissions import Perm

# The one operation open to any Dashboard user: it reports their own permissions, and
# an Owner who has not yet enrolled TOTP needs it to learn that they must.
NO_PERMISSION = {"staff_get_me"}
UNENROLLED_OWNER_MAY_CALL = {"staff_get_me"}

# Every operation that needs an Owner-only permission. Changing this set changes who
# may see or do what, so it should be a deliberate edit.
OWNER_ONLY_OPERATIONS: dict[str, Perm] = {
    "staff_list_users": Perm.MANAGE_STAFF_USERS,
    "staff_invite_user": Perm.MANAGE_STAFF_USERS,
    "staff_deactivate_user": Perm.MANAGE_STAFF_USERS,
    "audit_log_list": Perm.VIEW_AUDIT_LOG,
    "audit_log_get": Perm.VIEW_AUDIT_LOG,
    "catalog_update_product_class": Perm.MANAGE_SETTINGS,
    "catalog_create_colour": Perm.MANAGE_COLOURS,
    "catalog_update_colour": Perm.MANAGE_COLOURS,
    "catalog_list_suppliers": Perm.MANAGE_SUPPLIERS,
    "catalog_create_supplier": Perm.MANAGE_SUPPLIERS,
    "catalog_update_supplier": Perm.MANAGE_SUPPLIERS,
    "catalog_import_preview": Perm.IMPORT_CATALOGUE,
    "catalog_import_confirm": Perm.IMPORT_CATALOGUE,
    "settings_get": Perm.MANAGE_SETTINGS,
    "settings_update": Perm.MANAGE_SETTINGS,
    "account_deletions_list": Perm.MANAGE_ACCOUNT_DELETIONS,
    "account_deletions_complete": Perm.MANAGE_ACCOUNT_DELETIONS,
    "account_deletions_decline": Perm.MANAGE_ACCOUNT_DELETIONS,
    "shipping_get": Perm.MANAGE_SETTINGS,
    "shipping_update_rate": Perm.MANAGE_SETTINGS,
    "shipping_move_province": Perm.MANAGE_SETTINGS,
    "shipping_rename_zone": Perm.MANAGE_SETTINGS,
    "orders_refund": Perm.RECORD_REFUNDS,
    "orders_return_refund": Perm.RECORD_REFUNDS,
    "exports_accountant_csv": Perm.EXPORT_ACCOUNTANT_CSV,
    "legal_pages_list": Perm.MANAGE_LEGAL_PAGES,
    "legal_pages_versions": Perm.MANAGE_LEGAL_PAGES,
    "legal_pages_get_version": Perm.MANAGE_LEGAL_PAGES,
    "legal_pages_publish": Perm.MANAGE_LEGAL_PAGES,
}

# Owner-only permissions that no endpoint declares because a service checks them
# inside a shared endpoint (for example cancelling a paid Order).
CHECKED_IN_SERVICES = {
    Perm.ARCHIVE_PRODUCTS,
    Perm.CANCEL_PAID_ORDERS,
    Perm.VIEW_COST_PRICES,
}


def operations():
    """Yield `(path, operation)` for every operation the Dashboard API serves."""
    for _prefix, router in admin_api._routers:  # noqa: SLF001
        for path, view in router.path_operations.items():
            for operation in view.operations:
                yield path, operation


def declared(operation) -> RequiresPermission | None:
    callbacks = operation.auth_callbacks
    if len(callbacks) == 1 and isinstance(callbacks[0], RequiresPermission):
        return callbacks[0]
    return None


def test_the_api_has_operations():
    assert len(list(operations())) > 50  # noqa: PLR2004


def test_every_operation_declares_exactly_one_permission_check():
    undeclared = [op.operation_id for _, op in operations() if declared(op) is None]
    assert undeclared == []


def test_only_known_operations_are_open_to_every_dashboard_user():
    open_ops = {
        op.operation_id for _, op in operations() if declared(op).permission is None
    }
    assert open_ops == NO_PERMISSION


def test_only_known_operations_skip_the_owner_totp_check():
    skipping = {
        op.operation_id for _, op in operations() if declared(op).allow_unenrolled_owner
    }
    assert skipping == UNENROLLED_OWNER_MAY_CALL


def test_owner_only_operations_are_exactly_the_expected_ones():
    actual = {
        op.operation_id: declared(op).permission
        for _, op in operations()
        if declared(op).permission in OWNER_ONLY
    }
    assert actual == OWNER_ONLY_OPERATIONS


def test_owner_only_permissions_not_declared_on_an_endpoint_are_known():
    on_endpoints = {declared(op).permission for _, op in operations()}
    assert OWNER_ONLY - on_endpoints == CHECKED_IN_SERVICES


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "operation_id"),
    [
        (op.methods[0], path, op.operation_id)
        for path, op in operations()
        if op.operation_id in OWNER_ONLY_OPERATIONS
    ],
)
def test_staff_is_refused_on_every_owner_only_operation(
    api_for,
    staff_user,
    method,
    path,
    operation_id,
):
    url = "/api/admin" + re.sub(r"\{[^}]+\}", "1", path)
    response = getattr(api_for(staff_user), method.lower())(url)
    assert response.status_code == 403, operation_id  # noqa: PLR2004
