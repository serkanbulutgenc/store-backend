# ruff: noqa: TC001, TC003
"""The signed-in Customer's account: profile, address book, orders, deletion."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus

from ninja import Field
from ninja import Router
from ninja import Schema
from ninja.pagination import paginate

from store_backend.api.auth import CustomerAuth
from store_backend.api.money import Money
from store_backend.api.pagination import PageOffsetPagination
from store_backend.services import customers
from store_backend.services import tracking
from store_backend.shop.tracking_api import ReturnIn
from store_backend.shop.tracking_api import TrackingOut
from store_backend.shop.tracking_api import cancel_order
from store_backend.shop.tracking_api import order_out
from store_backend.shop.tracking_api import request_order_return

router = Router(tags=["account"], auth=CustomerAuth())


class AccountOut(Schema):
    id: int
    email: str
    name: str
    phone: str
    marketing_consent: bool
    deletion_requested: bool

    @staticmethod
    def resolve_marketing_consent(obj) -> bool:
        return obj.marketing_consent_at is not None

    @staticmethod
    def resolve_deletion_requested(obj) -> bool:
        return customers.has_pending_deletion(obj)


class AccountPatch(Schema):
    """Only the fields sent change. The email changes through allauth (re-verified)."""

    name: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=30)
    marketing_consent: bool | None = None


class SavedAddressIn(Schema):
    full_name: str = Field(max_length=150)
    phone: str = Field(max_length=30)
    il: str = Field(max_length=100)
    ilce: str = Field(max_length=100)
    mahalle: str = Field(max_length=150)
    street: str = Field(max_length=200)
    postal_code: str = Field("", max_length=10)
    is_default: bool = False


class SavedAddressOut(Schema):
    id: int
    full_name: str
    phone: str
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str
    is_default: bool


class AccountOrderRow(Schema):
    number: str
    placed_at: datetime
    status: str
    payment_status: str
    total: Money
    item_count: int

    @staticmethod
    def resolve_placed_at(obj) -> datetime:
        return obj.date_placed

    @staticmethod
    def resolve_payment_status(obj) -> str:
        return tracking.payment_status_of(obj)

    @staticmethod
    def resolve_total(obj):
        return obj.total_incl_tax

    @staticmethod
    def resolve_item_count(obj) -> int:
        return sum(line.quantity for line in obj.lines.all())


class OrderAddressOut(Schema):
    full_name: str
    phone: str
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str


class AccountOrderOut(TrackingOut):
    shipping_address: OrderAddressOut | None = None


@router.get("/account", operation_id="account_get", response=AccountOut)
def get_account(request):
    """The signed-in Customer. 401 when signed out, which is what the guard uses."""
    return request.auth


@router.patch("/account", operation_id="account_update", response=AccountOut)
def update_account(request, data: AccountPatch):
    return customers.update_profile(request.auth, **data.model_dump(exclude_unset=True))


@router.get(
    "/account/addresses",
    operation_id="account_list_addresses",
    response=list[SavedAddressOut],
)
def list_addresses(request):
    return customers.addresses_of(request.auth)


@router.post(
    "/account/addresses",
    operation_id="account_create_address",
    response={HTTPStatus.CREATED: SavedAddressOut},
)
def create_address(request, data: SavedAddressIn):
    """Up to five; the first one saved is the default. 409 `address_limit` beyond."""
    body = data.model_dump()
    default = body.pop("is_default")
    return HTTPStatus.CREATED, customers.add_address(
        request.auth,
        body,
        make_default=default,
    )


@router.put(
    "/account/addresses/{address_id}",
    operation_id="account_update_address",
    response=SavedAddressOut,
)
def update_address(request, address_id: int, data: SavedAddressIn):
    body = data.model_dump()
    default = body.pop("is_default")
    return customers.update_address(
        request.auth,
        address_id,
        body,
        make_default=default,
    )


@router.delete(
    "/account/addresses/{address_id}",
    operation_id="account_delete_address",
    response={HTTPStatus.NO_CONTENT: None},
)
def delete_address(request, address_id: int):
    customers.delete_address(request.auth, address_id)
    return HTTPStatus.NO_CONTENT, None


@router.get(
    "/account/orders",
    operation_id="account_list_orders",
    response=list[AccountOrderRow],
)
@paginate(PageOffsetPagination)
def list_orders(request):
    return customers.orders_of(request.auth).prefetch_related(
        "lines",
        "sources",
    )


@router.get(
    "/account/orders/{number}",
    operation_id="account_get_order",
    response=AccountOrderOut,
)
def get_order(request, number: str):
    order = customers.order_of(request.auth, number)
    address = order.shipping_address
    shipping = None
    if address:
        phone = (
            getattr(address.phone_number, "as_e164", "") if address.phone_number else ""
        )
        shipping = {
            "full_name": f"{address.first_name} {address.last_name}".strip(),
            "phone": phone,
            "il": address.state,
            "ilce": address.line4,
            "mahalle": address.line3,
            "street": address.line1,
            "postal_code": address.postcode,
        }
    return {**order_out(order), "shipping_address": shipping}


@router.post(
    "/account/orders/{number}/cancel",
    operation_id="account_cancel_order",
    response=TrackingOut,
)
def cancel_my_order(request, number: str):
    """Cancel one of my Orders while it is new or preparing."""
    return cancel_order(customers.order_of(request.auth, number).number)


@router.post(
    "/account/orders/{number}/returns",
    operation_id="account_request_return",
    response={HTTPStatus.CREATED: TrackingOut},
)
def return_my_order(request, number: str, data: ReturnIn):
    """Ask to return lines of one of my delivered Orders."""
    order = customers.order_of(request.auth, number)
    return HTTPStatus.CREATED, request_order_return(order.number, data)


@router.post(
    "/account/deletion-request",
    operation_id="account_request_deletion",
    response={HTTPStatus.CREATED: AccountOut},
)
def request_deletion(request):
    """Ask the Owner to delete the account. Personal fields are anonymised; Orders
    stay for the legal retention period. 409 while a request is already open."""
    customers.request_deletion(request.auth)
    return HTTPStatus.CREATED, request.auth
