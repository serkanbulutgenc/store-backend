"""The contact form: the topics to pick from and the submit."""

from __future__ import annotations

from http import HTTPStatus

from ninja import Field
from ninja import Router
from ninja import Schema

from store_backend.api.auth import GuestAuth
from store_backend.api.client import client_ip
from store_backend.services import contact_threads

router = Router(tags=["contact"], auth=GuestAuth())


class ContactCategoryOut(Schema):
    id: int
    name: str


class ContactIn(Schema):
    category_id: int
    name: str = Field(max_length=200)
    email: str = Field(max_length=254)
    order_number: str = Field(default="", max_length=100)
    message: str = Field(max_length=20000)
    website: str = Field(
        default="",
        max_length=200,
        description="Honeypot: a hidden field that real Shoppers leave empty.",
    )


@router.get(
    "/contact/categories",
    operation_id="contact_categories",
    response=list[ContactCategoryOut],
)
def list_categories(request):
    """The active topics, in the Owner's order."""
    return list(contact_threads.categories(only_active=True))


@router.post(
    "/contact",
    operation_id="contact_submit",
    response={HTTPStatus.CREATED: None},
)
def submit(request, data: ContactIn):
    """Send a message. Open to Guests and signed-in Shoppers; the Shopper gets an
    acknowledgement email. An order number that matches an Order links the thread to
    it, otherwise it is kept as typed. 422 for a bad field, 429 on too many tries from
    one address. A filled `website` honeypot answers 201 and stores nothing."""
    user = request.auth if request.auth.is_authenticated else None
    contact_threads.submit(
        category_id=data.category_id,
        name=data.name,
        email=data.email,
        message=data.message,
        order_number=data.order_number,
        honeypot=data.website,
        ip=client_ip(request),
        user=user,
    )
    return HTTPStatus.CREATED, None
