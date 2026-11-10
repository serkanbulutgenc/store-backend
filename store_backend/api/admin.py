"""Staff-only Dashboard API at /api/admin/.

Identified by the allauth session token (`X-Session-Token`), like the rest of the
Start proxy traffic. Each endpoint declares one named permission with
`auth=requires(Perm.X)`; an endpoint that does not is closed to everyone.
"""

from store_backend.api.base import create_api
from store_backend.api.permissions import UndeclaredPermission

admin_api = create_api(
    title="Aura Tekstil Admin API",
    urls_namespace="admin_api",
    auth=UndeclaredPermission(),
)

admin_api.add_router("/", "store_backend.staff.api.router")
admin_api.add_router("/", "store_backend.catalog.api.router")
admin_api.add_router("/", "store_backend.shop.api.router")
admin_api.add_router("/", "store_backend.shop.orders_api.router")
admin_api.add_router("/", "store_backend.shop.home_api.router")
