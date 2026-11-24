"""Basket Campaign management for the Owner (GLOSSARY.md, ADR 0011). Staff have no
access: it is the same permission as Campaigns."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal  # noqa: TC003
from typing import Literal

from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.money import Money  # noqa: TC001
from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.services import basket_campaigns
from store_backend.staff.permissions import Perm

router = Router(tags=["campaigns"])

CampaignStatus = Literal["active", "inactive", "scheduled", "ended"]
BenefitKind = Literal["fixed", "percentage", "free_shipping"]


class BasketCampaignIn(Schema):
    name: str
    minimum_amount: Decimal
    benefit: BenefitKind
    value: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True


class BasketCampaignPatch(Schema):
    """What the Owner changes; anything left out stays as it is."""

    name: str | None = None
    minimum_amount: Decimal | None = None
    benefit: BenefitKind | None = None
    value: Decimal | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class BasketCampaignOut(Schema):
    id: int
    name: str
    label: str
    minimum_amount: Money
    benefit: BenefitKind
    value: Money | None
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    status: CampaignStatus
    orders: int


@router.get(
    "/basket-campaigns",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_list",
    response=list[BasketCampaignOut],
)
@paginate(PageOffsetPagination)
def list_basket_campaigns(request, status: str | None = None):
    return basket_campaigns.listing(status)


@router.get(
    "/basket-campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_get",
    response=BasketCampaignOut,
)
def get_basket_campaign(request, campaign_id: int):
    return basket_campaigns.row(basket_campaigns.get(campaign_id))


@router.post(
    "/basket-campaigns",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_create",
    response={201: BasketCampaignOut},
)
def create_basket_campaign(request, data: BasketCampaignIn):
    made = basket_campaigns.create(request.auth, **data.model_dump())
    return 201, basket_campaigns.row(made)


@router.patch(
    "/basket-campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_edit",
    response=BasketCampaignOut,
)
def edit_basket_campaign(request, campaign_id: int, data: BasketCampaignPatch):
    given = data.model_dump(exclude_unset=True)
    # A name, minimum or benefit cannot be cleared; only the dates and the value can.
    blank = [
        key
        for key in ("name", "minimum_amount", "benefit")
        if key in given and given[key] is None
    ]
    for key in blank:
        del given[key]
    changed = basket_campaigns.edit(request.auth, campaign_id, **given)
    return basket_campaigns.row(changed)


@router.post(
    "/basket-campaigns/{campaign_id}/activate",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_activate",
    response=BasketCampaignOut,
)
def activate_basket_campaign(request, campaign_id: int):
    return basket_campaigns.row(basket_campaigns.activate(request.auth, campaign_id))


@router.post(
    "/basket-campaigns/{campaign_id}/deactivate",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_deactivate",
    response=BasketCampaignOut,
)
def deactivate_basket_campaign(request, campaign_id: int):
    return basket_campaigns.row(basket_campaigns.deactivate(request.auth, campaign_id))


@router.delete(
    "/basket-campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="basket_campaigns_delete",
    response={204: None},
)
def delete_basket_campaign(request, campaign_id: int):
    basket_campaigns.delete(request.auth, campaign_id)
    return 204, None
