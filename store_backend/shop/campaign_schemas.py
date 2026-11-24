# ruff: noqa: TC001
"""The Basket Campaign as the Shopper reads it, shared by the basket, the quote, the
placed Order and order tracking (GLOSSARY: Basket Campaign)."""

from __future__ import annotations

from ninja import Field
from ninja import Schema

from store_backend.api.money import Money


class AppliedCampaignOut(Schema):
    label: str = Field(description='Built from the rule: "500 ₺ üzeri 50 ₺ indirim".')
    discount: Money = Field(
        description="What it took off the items; 0 when it waives the shipping fee.",
    )
    free_shipping: bool = Field(description="True when it waives the shipping fee.")
