from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.utils import timezone
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import Condition
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.offer.models import Range
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import Voucher
from oscar.apps.voucher.models import VoucherApplication

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.shop.models import RateRow
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
            headers={"user-agent": user_agent},
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

    def put(self, path, data):
        return self._send("put", path, data)

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


@pytest.fixture
def computed_shipping():
    """Ask for this to keep the seeded carrier rates; see `flat_shipping`."""


@pytest.fixture(autouse=True)
def flat_shipping(request):
    """Most tests price shipping from the flat Settings fee; with no rates, it is."""
    if "computed_shipping" not in request.fixturenames:
        RateRow.objects.all().delete()


def make_voucher(  # noqa: PLR0913
    benefit_type: str,
    value: str,
    *,
    minimum: str = "0",
    code: str = "YAZ10",
    starts_in_days: int = -1,
    ends_in_days: int = 30,
    usage: str = Voucher.MULTI_USE,
) -> Voucher:
    """A Voucher as the Dashboard will store it: Oscar's voucher, offer and benefit."""
    everything = Range.objects.create(name=f"all-{code}", includes_all_products=True)
    offer = ConditionalOffer.objects.create(
        name=f"offer-{code}",
        offer_type=ConditionalOffer.VOUCHER,
        condition=Condition.objects.create(
            range=everything,
            type=Condition.VALUE,
            value=Decimal(minimum),
        ),
        benefit=Benefit.objects.create(
            range=everything,
            type=benefit_type,
            value=Decimal(value),
        ),
    )
    now = timezone.now()
    voucher = Voucher.objects.create(
        name=f"voucher-{code}",
        code=code,
        usage=usage,
        start_datetime=now + timedelta(days=starts_in_days),
        end_datetime=now + timedelta(days=ends_in_days),
    )
    voucher.offers.add(offer)
    return voucher


def redeem(voucher: Voucher, *, email: str = "ayse@example.com", user=None) -> Order:
    """One Redemption: an Order that used `voucher`, as placement will record it."""
    number = f"AT-T{VoucherApplication.objects.count() + 1:05d}"
    order = Order.objects.create(
        number=number,
        currency="TRY",
        total_incl_tax=Decimal("100.00"),
        total_excl_tax=Decimal("83.33"),
        guest_email=email,
        user=user,
    )
    voucher.record_usage(order, user or AnonymousUser())
    return order
