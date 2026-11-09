# ruff: noqa: PLR2004
from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.catalogue.models import ProductClass
from oscar.apps.partner.models import StockRecord

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import VariantProfile
from store_backend.services import catalog

pytestmark = pytest.mark.django_db

BASKET = "/api/store/basket"
LINES = "/api/store/basket/lines"


class Guest:
    """A browser with a CSRF token and whatever cookies Django sets."""

    def __init__(self, token: str | None = None):
        self.client = Client(enforce_csrf_checks=True)
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


def test_guest_adds_reads_and_the_cookie_restores_the_basket(shop):
    _, v = shop
    guest = Guest()

    response = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2})

    assert response.status_code == HTTPStatus.OK, response.content
    assert "oscar_open_basket" in response.cookies
    body = response.json()
    assert body["item_count"] == 2
    assert body["subtotal"] == "200.00"
    line = body["lines"][0]
    assert (line["sku"], line["size"], line["unit_price"], line["line_total"]) == (
        v["M"].variant_profile.sku,
        "M",
        "100.00",
        "200.00",
    )
    # A new request carrying only the cookie sees the same basket.
    again = Guest()
    again.client.cookies = guest.client.cookies
    assert again.get(BASKET).json()["item_count"] == 2


def test_adding_the_same_variant_grows_the_line(shop):
    _, v = shop
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk})

    body = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2}).json()

    assert len(body["lines"]) == 1
    assert body["lines"][0]["quantity"] == 3


def test_quantity_above_stock_is_a_409_envelope(shop):
    _, v = shop
    guest = Guest()

    response = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 6})

    assert response.status_code == HTTPStatus.CONFLICT
    body = response.json()
    assert body["code"] == "insufficient_stock"
    assert "5" in body["message"]
    assert "quantity" in body["fields"]


def test_the_running_total_counts_against_stock(shop):
    _, v = shop
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 4})

    response = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2})

    assert response.status_code == HTTPStatus.CONFLICT


def test_reserved_units_do_not_count_as_available(shop):
    _, v = shop
    StockRecord.objects.filter(product=v["M"]).update(num_allocated=4)

    response = Guest().post(LINES, {"variant_id": v["M"].pk, "quantity": 2})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "insufficient_stock"


def test_sold_out_variant_cannot_be_added(shop):
    _, v = shop

    response = Guest().post(LINES, {"variant_id": v["L"].pk})

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["code"] == "sold_out"


def test_unknown_draft_and_parent_ids_are_404(shop):
    product, v = shop
    ProductProfile.objects.filter(product=product).update(status=ProductStatus.DRAFT)
    guest = Guest()

    for pk in (v["M"].pk, product.pk, 999999):
        assert guest.post(LINES, {"variant_id": pk}).status_code == HTTPStatus.NOT_FOUND


def test_change_quantity_and_remove(shop):
    _, v = shop
    guest = Guest()
    line_id = guest.post(LINES, {"variant_id": v["M"].pk}).json()["lines"][0]["id"]

    body = guest.patch(f"{LINES}/{line_id}", {"quantity": 4}).json()
    assert body["lines"][0]["quantity"] == 4
    assert guest.patch(f"{LINES}/{line_id}", {"quantity": 6}).status_code == 409
    assert guest.patch(f"{LINES}/{line_id}", {"quantity": 0}).status_code == 422

    body = guest.delete(f"{LINES}/{line_id}").json()
    assert body["lines"] == []
    assert body["subtotal"] == "0.00"
    assert guest.delete(f"{LINES}/{line_id}").status_code == HTTPStatus.NOT_FOUND


def test_lines_of_another_basket_are_not_reachable(shop):
    _, v = shop
    line_id = Guest().post(LINES, {"variant_id": v["M"].pk}).json()["lines"][0]["id"]

    other = Guest()

    assert other.patch(f"{LINES}/{line_id}", {"quantity": 2}).status_code == 404
    assert other.delete(f"{LINES}/{line_id}").status_code == 404


def test_a_guest_without_a_csrf_token_is_refused(shop):
    _, v = shop
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        LINES,
        {"variant_id": v["M"].pk},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "csrf_failed"


def test_baskets_never_reserve_stock(shop):
    _, v = shop
    Guest().post(LINES, {"variant_id": v["M"].pk, "quantity": 5})

    record = StockRecord.objects.get(product=v["M"])
    assert (record.num_in_stock, record.num_allocated or 0) == (5, 0)
    assert (
        Guest().post(LINES, {"variant_id": v["M"].pk, "quantity": 5}).status_code == 200
    )


def test_lines_flag_problems_when_stock_or_publication_changes(shop):
    product, v = shop
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 3})

    StockRecord.objects.filter(product=v["M"]).update(num_in_stock=2)
    assert guest.get(BASKET).json()["lines"][0]["problem"] == "insufficient_stock"

    StockRecord.objects.filter(product=v["M"]).update(num_in_stock=0)
    assert guest.get(BASKET).json()["lines"][0]["problem"] == "sold_out"

    ProductProfile.objects.filter(product=product).update(status=ProductStatus.ARCHIVED)
    body = guest.get(BASKET).json()
    assert body["lines"][0]["problem"] == "unavailable"
    assert body["has_problems"] is True


def test_a_variant_price_override_drives_the_line_price(shop):
    _, v = shop
    VariantProfile.objects.filter(product=v["M"]).update(price_override="80.00")
    guest = Guest()

    body = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2}).json()

    assert body["lines"][0]["unit_price"] == "80.00"
    assert body["subtotal"] == "160.00"


def test_login_merges_the_guest_basket_into_the_customer_basket(shop, customer, log_in):
    _, v = shop
    guest = Guest()
    guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 2})
    token = log_in(Client(), customer)

    signed_in = Guest(token)
    signed_in.client.cookies = guest.client.cookies
    body = signed_in.get(BASKET).json()

    assert body["item_count"] == 2
    # Next request needs no cookie: the basket belongs to the customer now.
    assert Guest(token).get(BASKET).json()["item_count"] == 2


def test_a_line_that_outgrew_its_stock_can_be_lowered_but_not_raised(shop):
    _, v = shop
    guest = Guest()
    line_id = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 5}).json()[
        "lines"
    ][0]["id"]
    StockRecord.objects.filter(product=v["M"]).update(num_in_stock=2)

    assert guest.patch(f"{LINES}/{line_id}", {"quantity": 4}).status_code == 200
    assert guest.patch(f"{LINES}/{line_id}", {"quantity": 5}).status_code == 409


def test_an_unavailable_line_can_still_be_reduced_or_removed(shop):
    product, v = shop
    guest = Guest()
    line_id = guest.post(LINES, {"variant_id": v["M"].pk, "quantity": 3}).json()[
        "lines"
    ][0]["id"]
    ProductProfile.objects.filter(product=product).update(status=ProductStatus.ARCHIVED)

    assert guest.patch(f"{LINES}/{line_id}", {"quantity": 1}).status_code == 200
    assert guest.delete(f"{LINES}/{line_id}").status_code == 200
