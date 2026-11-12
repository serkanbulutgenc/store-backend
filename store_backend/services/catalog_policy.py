"""Owner-only rules for the Dashboard catalog, in one place.

Cost price, Supplier and supplier code are Owner-only: rejected on input for anyone
else (even to clear them), and left out of responses (not nulled). Archiving and
unarchiving a Product is Owner-only too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from store_backend.api.errors import ForbiddenError
from store_backend.catalog.models import ProductStatus
from store_backend.staff.permissions import Perm
from store_backend.staff.permissions import omit_owner_only

if TYPE_CHECKING:
    from collections.abc import Mapping

    from oscar.apps.catalogue.models import Product

OWNER_PRODUCT_FIELDS = ("cost_price", "supplier", "supplier_code")
OWNER_INPUT_FIELDS = ("cost_price", "supplier_id", "supplier_code")


def _can(user: Any, perm: Perm) -> bool:
    return user.has_perm(perm.permission_name)


def require_owner_fields_allowed(user: Any, data: Mapping[str, Any]) -> None:
    """Staff may not send cost price or Supplier data, even to clear it, nor a
    product's own desi (the Owner owns shipping size, like the rate table)."""
    if any(f in data for f in OWNER_INPUT_FIELDS) and not _can(
        user,
        Perm.VIEW_COST_PRICES,
    ):
        raise ForbiddenError
    if "desi" in data and not _can(user, Perm.MANAGE_SETTINGS):
        raise ForbiddenError


def omit_owner_fields(user: Any, payload: Mapping[str, Any]) -> dict:
    """The product payload without Owner-only fields unless the user may see them."""
    return omit_owner_only(user, payload, *OWNER_PRODUCT_FIELDS)


def require_status_change_allowed(
    user: Any,
    product: Product,
    new_status: ProductStatus,
) -> None:
    """Publishing and returning to draft are open; archiving and unarchiving are not."""
    touches_archive = ProductStatus.ARCHIVED in (new_status, product.profile.status)
    if touches_archive and not _can(user, Perm.ARCHIVE_PRODUCTS):
        raise ForbiddenError
