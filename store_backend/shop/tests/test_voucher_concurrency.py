"""Two Shoppers racing for a Voucher's last Redemption.

Same approach as `test_checkout_concurrency`: real commits on separate connections,
all database work in short-lived threads, and everything they made deleted again.
"""

from __future__ import annotations

import threading

import pytest
from oscar.apps.basket.models import Basket
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.offer.models import Benefit
from oscar.apps.offer.models import Condition
from oscar.apps.offer.models import ConditionalOffer
from oscar.apps.offer.models import Range
from oscar.apps.order.models import Order
from oscar.apps.voucher.models import Voucher
from oscar.apps.voucher.models import VoucherApplication

from store_backend.api.errors import ConflictError
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import catalog
from store_backend.services import vouchers
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.conftest import make_voucher
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout_concurrency import _purge
from store_backend.shop.tests.test_checkout_concurrency import in_thread
from store_backend.shop.tests.test_voucher_checkout import Selector
from store_backend.shop.tests.test_voucher_checkout import place

pytestmark = pytest.mark.django_db


def _purge_vouchers() -> None:
    VoucherApplication.objects.all().delete()
    Voucher.objects.all().delete()
    ConditionalOffer.objects.all().delete()
    Benefit.objects.all().delete()
    Condition.objects.all().delete()
    Range.objects.all().delete()


@pytest.fixture(autouse=True)
def _committed():
    in_thread(_purge_vouchers)
    in_thread(_purge)
    yield
    in_thread(_purge_vouchers)
    in_thread(_purge)


def _setup() -> list[int]:
    colour = catalog.create_colour("Siyah", "#000000")
    product = catalog.create_product(
        title="Elbise",
        product_class=ProductClass.objects.get(name="Giyim"),
        category=catalog.create_category("Giyim"),
        shelf_price="100.00",
    )
    # One variant each: sharing a stock row would serialise the two checkouts and hide
    # the race this test is about.
    variants = [
        catalog.create_variant(product, colour=colour, size=size, stock=5)
        for size in ("M", "L")
    ]
    ProductProfile.objects.filter(product=product).update(
        status=ProductStatus.PUBLISHED,
    )
    voucher = make_voucher("Percentage", "10", code="LAST10")
    voucher.offers.update(max_global_applications=1)
    guests = [Guest(), Guest(ip="203.0.113.8")]
    for g, variant in zip(guests, variants, strict=True):
        fill(g, variant, 1)
    return list(Basket.objects.order_by("pk").values_list("pk", flat=True))


def _place(barrier, basket_pk: int, email: str) -> str:
    basket = Basket.objects.get(pk=basket_pk)
    basket.strategy = Selector().strategy()
    barrier.wait()
    try:
        place(basket, "LAST10", email=email)
    except ConflictError as error:
        return error.fields["voucher_code"][0]
    return "placed"


def _outcome() -> tuple[int, int, int]:
    voucher = Voucher.objects.get()
    return Order.objects.count(), voucher.applications.count(), voucher.num_orders


def test_two_concurrent_orders_for_the_last_redemption():
    baskets = in_thread(_setup)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def run(pk, email):
        results.append(in_thread(_place, barrier, pk, email))

    racers = [
        threading.Thread(target=run, args=(pk, f"shopper{i}@example.com"))
        for i, pk in enumerate(baskets)
    ]
    for t in racers:
        t.start()
    for t in racers:
        t.join()

    assert sorted(results) == [vouchers.Reason.EXHAUSTED, "placed"]
    assert in_thread(_outcome) == (1, 1, 1)
