"""Stock arithmetic and reservations live behind `services.stock`: what is free to
sell, which record counts for a Variant, and releasing or consuming an Order's units."""

# ruff: noqa: PLR2004
import pytest
from oscar.apps.catalogue.models import Product
from oscar.apps.order.models import Order
from oscar.apps.partner.models import Partner
from oscar.apps.partner.models import StockRecord

from store_backend.api.errors import ConflictError
from store_backend.services import stock
from store_backend.shop.tests.test_orders import place

pytestmark = pytest.mark.django_db


def record_of(variant) -> StockRecord:
    return StockRecord.objects.get(product=variant)


def test_units_are_on_hand_minus_reserved_and_never_negative():
    assert stock.units(None) == 0
    assert stock.units(StockRecord(num_in_stock=5, num_allocated=2)) == 3
    assert stock.units(StockRecord(num_in_stock=5, num_allocated=None)) == 5
    assert stock.units(StockRecord(num_in_stock=1, num_allocated=3)) == 0


def test_low_means_at_or_below_the_threshold_counting_reserved_units():
    record = StockRecord(num_in_stock=5, num_allocated=2)  # 3 free
    assert stock.is_low(record, 3)
    assert not stock.is_low(record, 2)
    assert stock.is_low(None, 0)  # nothing to sell is always low


def test_the_sql_expression_agrees_with_units(shop):
    _, variants = shop
    StockRecord.objects.filter(product=variants["M"]).update(num_allocated=2)
    free = dict(
        Product.objects.filter(pk__in=[v.pk for v in variants.values()])
        .annotate(free=stock.available_sql("stockrecords__"))
        .values_list("pk", "free"),
    )
    assert free[variants["M"].pk] == stock.units(record_of(variants["M"])) == 3
    assert free[variants["L"].pk] == 0


def test_the_first_record_by_id_is_the_one_that_counts(shop):
    _, variants = shop
    variant = variants["M"]
    first = record_of(variant)
    second = StockRecord.objects.create(
        product=variant,
        partner=Partner.objects.create(name="Other"),
        partner_sku="OTHER-1",
        num_in_stock=99,
        num_allocated=0,
        price_currency="TRY",
    )

    assert stock.record_for(variant) == first
    assert stock.records_for([variant.pk]) == {variant.pk: first}
    assert stock.records_for([variant.pk], lock=True) == {variant.pk: first}
    assert second.pk > first.pk
    assert stock.records_for([]) == {}


def test_release_gives_an_orders_reserved_units_back(shop):
    _, variants = shop
    number = place(variants["M"])  # 2 units
    assert record_of(variants["M"]).num_allocated == 2

    stock.release(Order.objects.get(number=number))

    record = record_of(variants["M"])
    assert (record.num_in_stock, record.num_allocated) == (5, 0)


def test_consume_takes_the_units_off_the_shelf_and_the_reservation(shop):
    _, variants = shop
    number = place(variants["M"])

    stock.consume(Order.objects.get(number=number))

    record = record_of(variants["M"])
    assert (record.num_in_stock, record.num_allocated) == (3, 0)


def test_consume_refuses_when_the_reservation_is_missing(shop):
    _, variants = shop
    number = place(variants["M"])
    StockRecord.objects.filter(product=variants["M"]).update(num_allocated=0)

    with pytest.raises(ConflictError) as raised:
        stock.consume(Order.objects.get(number=number))

    assert raised.value.code == "stock_mismatch"
    assert record_of(variants["M"]).num_in_stock == 5
