"""An Oscar offer `Applicator` that applies nothing.

Prices come from `services.pricing` and `services.quote`, and a Voucher's discount is
computed there (ADR 0006). Oscar's basket middleware would otherwise apply the offer of
every Voucher on a basket by itself, giving the Order a second, differently rounded
discount.
"""

from __future__ import annotations

from oscar.apps.offer.applicator import Applicator as OscarApplicator


class Applicator(OscarApplicator):
    def apply(self, basket, user=None, request=None) -> None:
        """Leave the basket as it is."""
