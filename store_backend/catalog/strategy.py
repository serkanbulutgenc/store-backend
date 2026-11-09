"""Oscar strategy: prices are the KDV-inclusive shelf price, KDV split out of it.

Oscar's stock record price is not used. The shelf price lives on the Product (with a
Variant override), so price changes need no stock-record sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oscar.apps.partner import strategy
from oscar.apps.partner.prices import TaxInclusiveFixedPrice
from oscar.apps.partner.prices import Unavailable

from store_backend.services import pricing

if TYPE_CHECKING:
    from decimal import Decimal

    from oscar.apps.catalogue.models import Product

CURRENCY = "TRY"


def _price_for(variant: Product):
    # Imported here: the services module imports catalog models at load time.
    from store_backend.services import catalog  # noqa: PLC0415

    if not hasattr(variant, "variant_profile"):
        return None
    incl: Decimal = catalog.shelf_price_for(variant)
    excl, kdv = pricing.split_kdv(incl, catalog.kdv_rate_for(variant))
    return TaxInclusiveFixedPrice(currency=CURRENCY, excl_tax=excl, tax=kdv)


class KdvInclusiveStrategy(
    strategy.UseFirstStockRecord,
    strategy.StockRequired,
    strategy.Structured,
):
    def pricing_policy(self, product, stockrecord):
        if stockrecord is None:
            return Unavailable()
        return _price_for(product) or Unavailable()

    def parent_pricing_policy(self, product, children_stock):
        prices = [
            p
            for child, record in children_stock
            if record is not None and (p := _price_for(child)) is not None
        ]
        if not prices:
            return Unavailable()
        return min(prices, key=lambda p: p.incl_tax)


class Selector:
    def strategy(self, request=None, user=None, **kwargs):
        return KdvInclusiveStrategy(request)
