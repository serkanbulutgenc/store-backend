# ruff: noqa: TC001
"""Public read of the safe subset of Store settings."""

from __future__ import annotations

from ninja import Router
from ninja import Schema

from store_backend.api.money import Money
from store_backend.services import legal_pages
from store_backend.services import shop_settings

router = Router(tags=["settings"])


class SellerOut(Schema):
    name: str
    tax_number: str
    address: str
    email: str
    phone: str
    return_carrier: str
    complaint_contact: str


class PublicSettingsOut(Schema):
    shipping_fee: Money
    free_shipping_threshold: Money | None
    cod_fee: Money
    seller: SellerOut
    legal_versions: dict[str, str]
    stock_alerts_enabled: bool


@router.get(
    "/settings",
    auth=None,
    operation_id="settings_get_public",
    response=PublicSettingsOut,
)
def get_public_settings(request):
    """What the storefront needs: fees, seller identity, Legal versions and
    whether Stock alerts are on.
    Bank details, KDV rate, recipients and the stock default stay private.
    """
    s = shop_settings.get()
    return {
        "shipping_fee": s.shipping_fee,
        "free_shipping_threshold": s.free_shipping_threshold,
        "cod_fee": s.cod_fee,
        "seller": {
            "name": s.seller_name,
            "tax_number": s.seller_tax_number,
            "address": s.seller_address,
            "email": s.seller_email,
            "phone": s.seller_phone,
            "return_carrier": s.return_carrier,
            "complaint_contact": s.complaint_contact,
        },
        "legal_versions": legal_pages.current_versions(),
        "stock_alerts_enabled": s.stock_alerts_enabled,
    }
