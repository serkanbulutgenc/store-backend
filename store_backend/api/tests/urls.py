"""Project URLs plus test-only routes that exercise middleware and auth classes."""

from django.http import JsonResponse
from django.urls import path
from ninja import Router

from config.urls import urlpatterns as project_urlpatterns
from store_backend.api.auth import CustomerAuth
from store_backend.api.auth import GuestAuth
from store_backend.api.base import create_api


def basket_view(request):
    """Plain Django view, so the whole middleware stack runs (incl. basket merge)."""
    basket = request.basket
    return JsonResponse(
        {
            "user": request.user.pk,
            "owner": basket.owner_id,
            "basket_id": basket.id,
            "products": sorted(line.product_id for line in basket.all_lines()),
        },
    )


guest_api = create_api(title="Guest", urls_namespace="guest_api")
router = Router()


@router.post("/guest", auth=GuestAuth(), operation_id="t_guest", response={200: dict})
def guest(request):
    return {"user": request.auth.pk}


@router.post(
    "/customer",
    auth=CustomerAuth(),
    operation_id="t_customer",
    response={200: dict},
)
def customer(request):
    return {"user": request.auth.pk}


guest_api.add_router("/", router)

urlpatterns = [
    path("t/basket", basket_view),
    path("t/api/", guest_api.urls),
    *project_urlpatterns,
]
