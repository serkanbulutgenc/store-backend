"""The signed-in Shopper's Favorites."""

from __future__ import annotations

from http import HTTPStatus

from ninja import Router

from store_backend.api.auth import CustomerAuth
from store_backend.catalog.store_api import ProductCardOut
from store_backend.services import favorites

router = Router(tags=["account"], auth=CustomerAuth())


@router.get(
    "/account/favorites",
    operation_id="account_list_favorites",
    response=list[ProductCardOut],
)
def list_favorites(request):
    """Hearted Products, newest first (at most 200). Draft and archived Products are
    left out; sold-out ones come back with `sold_out` set so the UI can grey them."""
    return favorites.cards(request.auth)


@router.put(
    "/account/favorites/{product_id}",
    operation_id="account_add_favorite",
    response={HTTPStatus.NO_CONTENT: None},
)
def add_favorite(request, product_id: int):
    """Heart a Product; idempotent. 404 for unknown or unpublished Products, 409
    `favorites_limit` past 200."""
    favorites.add(request.auth, product_id)
    return HTTPStatus.NO_CONTENT, None


@router.delete(
    "/account/favorites/{product_id}",
    operation_id="account_remove_favorite",
    response={HTTPStatus.NO_CONTENT: None},
)
def remove_favorite(request, product_id: int):
    favorites.remove(request.auth, product_id)
    return HTTPStatus.NO_CONTENT, None
