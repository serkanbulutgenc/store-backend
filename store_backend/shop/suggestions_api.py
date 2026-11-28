"""Search suggestions under the Storefront search box. Public."""

from __future__ import annotations

from typing import Literal

from ninja import Router
from ninja import Schema

from store_backend.services import suggestions

router = Router(tags=["search"], auth=None)


class SuggestionOut(Schema):
    name: str
    path: str
    image_url: str | None


class SuggestionGroupOut(Schema):
    type: Literal["category", "brand", "collection", "campaign_page", "product"]
    items: list[SuggestionOut]


class SuggestionsOut(Schema):
    groups: list[SuggestionGroupOut]


@router.get(
    "/suggestions",
    operation_id="search_list_suggestions",
    response=SuggestionsOut,
)
def list_suggestions(request, q: str = ""):
    """What to offer under the search box for what has been typed so far.

    Under 2 characters gives no groups and only the first 100 count. 503
    `suggestions_unavailable` when search is switched off, unreachable or not yet
    indexed: show nothing.
    """
    return {"groups": suggestions.suggest(q)}
