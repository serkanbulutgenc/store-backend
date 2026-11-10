"""Two Shoppers racing for the last unit.

This needs real commits on separate connections, which a rolled-back test transaction
cannot give. `transaction=True` would work but flushes every table at the end, wiping
the migration-seeded rows from a kept (`--reuse-db`) test database. So the test does
all its database work in short-lived threads, each with its own autocommit connection,
and deletes exactly what it made. The test's own (rolled-back) transaction stays idle.
"""

import threading
from http import HTTPStatus

import pytest
from django.db import connection
from oscar.apps.address.models import Country
from oscar.apps.basket.models import Basket
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.order.models import BillingAddress
from oscar.apps.order.models import Order
from oscar.apps.order.models import ShippingAddress
from oscar.apps.partner.models import Partner
from oscar.apps.partner.models import StockRecord
from oscar.apps.payment.models import SourceType

from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.messaging.models import Message
from store_backend.services import catalog
from store_backend.shop.models import ConsentRecord
from store_backend.shop.models import OrderFeeSnapshot
from store_backend.shop.models import OrderLineSnapshot
from store_backend.shop.models import PreInformationSnapshot
from store_backend.shop.models import StoreSettings
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload

pytestmark = pytest.mark.django_db


def in_thread(fn, *args):
    """Run `fn` on its own connection, committing for real. Re-raises its error."""
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _purge() -> None:
    for model in (
        Message,
        OrderFeeSnapshot,
        OrderLineSnapshot,
        ConsentRecord,
        PreInformationSnapshot,
        Order,
        BillingAddress,
        ShippingAddress,
        Basket,
        SourceType,
        Country,
        StockRecord,
        StoreSettings,
    ):
        model.objects.all().delete()
    Product.objects.filter(structure=Product.CHILD).delete()
    Product.objects.all().delete()
    # Oscar's delete receiver needs sorl-thumbnail, which this project does not use.
    Category.objects.all()._raw_delete(Category.objects.db)  # noqa: SLF001
    Colour.objects.all().delete()
    Partner.objects.all().delete()


@pytest.fixture(autouse=True)
def _committed():
    in_thread(_purge)  # in case an earlier run died half way
    yield
    in_thread(_purge)


def _setup() -> tuple[int, list[Guest]]:
    colour = catalog.create_colour("Siyah", "#000000")
    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Giyim"),
        shelf_price="100.00",
    )
    last = catalog.create_variant(product, colour=colour, size="M", stock=1)
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )
    guests = [Guest(), Guest(ip="203.0.113.8")]
    for g in guests:
        fill(g, last, 1)
    return last.pk, guests


def _place(barrier, guest) -> int:
    barrier.wait()
    return guest.post(CHECKOUT, payload()).status_code


def _outcome(variant_id):
    record = StockRecord.objects.get(product_id=variant_id)
    return Order.objects.count(), record.num_in_stock, record.num_allocated


def test_two_concurrent_orders_for_the_last_unit():
    variant_id, guests = in_thread(_setup)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def run(g):
        statuses.append(in_thread(_place, barrier, g))

    racers = [threading.Thread(target=run, args=(g,)) for g in guests]
    for t in racers:
        t.start()
    for t in racers:
        t.join()

    assert sorted(statuses) == [HTTPStatus.CREATED, HTTPStatus.CONFLICT]
    assert in_thread(_outcome, variant_id) == (1, 1, 1)
