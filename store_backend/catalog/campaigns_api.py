"""Campaign management for the Owner (GLOSSARY.md, ADR 0009). Staff have no access."""

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
from store_backend.services import campaign_admin
from store_backend.staff.permissions import Perm

router = Router(tags=["campaigns"])

CampaignStatus = Literal["active", "inactive", "scheduled", "ended"]


class CampaignIn(Schema):
    name: str
    percentage: Decimal
    product_ids: list[int] = []
    category_ids: list[int] = []
    collection_ids: list[int] = []
    include_subcategories: bool = False
    whole_store: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True


class CampaignPatch(Schema):
    """What the Owner changes; anything left out stays as it is."""

    name: str | None = None
    percentage: Decimal | None = None
    product_ids: list[int] | None = None
    category_ids: list[int] | None = None
    collection_ids: list[int] | None = None
    include_subcategories: bool | None = None
    whole_store: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class CampaignRowOut(Schema):
    id: int
    name: str
    percentage: Money
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    whole_store: bool
    status: CampaignStatus


class TargetOut(Schema):
    id: int
    name: str


class BelowCostOut(Schema):
    product_id: int
    title: str
    campaign_price: Money
    cost_price: Money


class CampaignOut(CampaignRowOut):
    include_subcategories: bool
    products: list[TargetOut]
    categories: list[TargetOut]
    collections: list[TargetOut]
    affected_products: int
    below_cost: list[BelowCostOut]


@router.get(
    "/campaigns",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_list",
    response=list[CampaignRowOut],
)
@paginate(PageOffsetPagination)
def list_campaigns(request, status: str | None = None):
    return campaign_admin.listing(status)


@router.get(
    "/campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_get",
    response=CampaignOut,
)
def get_campaign(request, campaign_id: int):
    return campaign_admin.view(campaign_admin.get(campaign_id))


@router.post(
    "/campaigns",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_create",
    response={201: CampaignOut},
)
def create_campaign(request, data: CampaignIn):
    made = campaign_admin.create(request.auth, **data.model_dump())
    return 201, campaign_admin.view(made)


@router.patch(
    "/campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_edit",
    response=CampaignOut,
)
def edit_campaign(request, campaign_id: int, data: CampaignPatch):
    changed = campaign_admin.edit(
        request.auth,
        campaign_id,
        **data.model_dump(exclude_unset=True),
    )
    return campaign_admin.view(changed)


@router.post(
    "/campaigns/{campaign_id}/activate",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_activate",
    response=CampaignOut,
)
def activate_campaign(request, campaign_id: int):
    return campaign_admin.view(campaign_admin.activate(request.auth, campaign_id))


@router.post(
    "/campaigns/{campaign_id}/deactivate",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_deactivate",
    response=CampaignOut,
)
def deactivate_campaign(request, campaign_id: int):
    return campaign_admin.view(campaign_admin.deactivate(request.auth, campaign_id))


@router.delete(
    "/campaigns/{campaign_id}",
    auth=requires(Perm.MANAGE_CAMPAIGNS),
    operation_id="campaigns_delete",
    response={204: None},
)
def delete_campaign(request, campaign_id: int):
    campaign_admin.delete(request.auth, campaign_id)
    return 204, None
