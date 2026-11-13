"""Public storefront API at /api/store/ (catalog, basket, checkout, ...)."""

from django.middleware.csrf import get_token
from ninja import Schema

from store_backend.api.base import create_api

store_api = create_api(title="Aura Tekstil Store API", urls_namespace="store_api")


class CsrfOut(Schema):
    csrf_token: str


@store_api.get("/csrf", auth=None, operation_id="auth_get_csrf", response=CsrfOut)
def get_csrf(request):
    """CSRF token for guest endpoints. The cookie is HttpOnly, so JS cannot read it."""
    return {"csrf_token": get_token(request)}


store_api.add_router("/", "store_backend.catalog.store_api.router")
store_api.add_router("/", "store_backend.shop.store_api.router")
store_api.add_router("/", "store_backend.shop.legal_api.router")
store_api.add_router("/", "store_backend.shop.basket_api.router")
store_api.add_router("/", "store_backend.shop.checkout_api.router")
store_api.add_router("/", "store_backend.shop.tracking_api.router")
store_api.add_router("/", "store_backend.shop.account_api.router")
store_api.add_router("/", "store_backend.shop.favorites_api.router")
store_api.add_router("/", "store_backend.shop.reviews_api.router")
