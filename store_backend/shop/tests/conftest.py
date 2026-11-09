import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401


class Guest:
    """A browser with a CSRF token and whatever cookies Django sets."""

    def __init__(
        self,
        token: str | None = None,
        *,
        ip: str = "203.0.113.7",
        user_agent: str = "TestBrowser/1.0",
    ):
        self.client = Client(
            enforce_csrf_checks=True,
            REMOTE_ADDR=ip,
            HTTP_USER_AGENT=user_agent,
        )
        self.headers = {"X-Session-Token": token} if token else {}
        if not token:
            csrf = self.client.get("/api/store/csrf").json()["csrf_token"]
            self.headers["X-CSRFToken"] = csrf

    def _send(self, method, path, data=None):
        return getattr(self.client, method)(
            path,
            data,
            content_type="application/json",
            headers=self.headers,
        )

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def post(self, path, data=None):
        return self._send("post", path, data or {})

    def patch(self, path, data):
        return self._send("patch", path, data)

    def delete(self, path):
        return self.client.delete(path, headers=self.headers)


@pytest.fixture
def shop():
    colour = catalog.create_colour("Siyah", "#000000")
    cat = catalog.create_category("Giyim")
    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=cat,
        shelf_price="100.00",
    )
    variants = {
        size: catalog.create_variant(product, colour=colour, size=size, stock=stock)
        for size, stock in (("M", 5), ("L", 0))
    }
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )
    return product, variants
