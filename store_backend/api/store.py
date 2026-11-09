"""Public storefront API at /api/store/ (catalog, basket, checkout, ...)."""

from store_backend.api.base import create_api

store_api = create_api(title="Aura Tekstil Store API", urls_namespace="store_api")
