"""Envelope, pagination, ordering, money and operationId conventions."""

from decimal import Decimal
from http import HTTPStatus

import pytest
from django.contrib.auth.models import Group
from django.http import Http404
from ninja import Query
from ninja import Router
from ninja import Schema
from ninja.errors import HttpError
from ninja.pagination import paginate
from ninja.testing import TestClient

from store_backend.api.base import create_api
from store_backend.api.errors import ConflictError
from store_backend.api.filters import QueryFilterSchema
from store_backend.api.money import Money  # noqa: TC001 (pydantic resolves it at runtime)
from store_backend.api.ordering import OrderingError
from store_backend.api.ordering import apply_ordering
from store_backend.api.pagination import PageOffsetPagination

pytestmark = pytest.mark.django_db


class PriceOut(Schema):
    price: Money


class NameIn(Schema):
    name: str
    qty: int


router = Router()


@router.get("/conflict", operation_id="test_conflict", response={200: dict})
def conflict(request):
    msg = "Stok tükendi."
    raise ConflictError(msg, code="stock_gone", fields={"sku": ["Stokta yok."]})


@router.post("/validate", operation_id="test_validate", response={200: dict})
def validate(request, data: NameIn):
    return {}


@router.get("/missing", operation_id="test_missing", response={200: dict})
def missing(request):
    raise Http404


@router.get("/boom", operation_id="test_boom", response={200: dict})
def boom(request):
    msg = "secret internals"
    raise RuntimeError(msg)


@router.get("/throttled", operation_id="test_throttled", response={200: dict})
def throttled(request):
    raise HttpError(429, "Too many requests.")


@router.get("/teapot", operation_id="test_teapot", response={200: dict})
def teapot(request):
    raise HttpError(405, "Method not allowed.")


@router.get("/price", operation_id="test_price", response=PriceOut)
def price(request):
    return {"price": Decimal("349.9")}


@router.get("/groups", operation_id="test_groups", response=list[dict])
@paginate(PageOffsetPagination)
def groups(request, filters: Query[QueryFilterSchema]):
    return Group.objects.order_by("name").values("id", "name")


@router.get("/ordered", operation_id="test_ordered", response=list[dict])
def ordered(request, ordering: str = "name"):
    qs = apply_ordering(Group.objects.all(), ordering, allowed={"name", "id"})
    return list(qs.values("name"))


@pytest.fixture(autouse=True)
def _no_seeded_groups(db):
    # Oscar seeds a Group in a migration; these tests count rows.
    Group.objects.all().delete()


@pytest.fixture
def client():
    api = create_api(title="Test", urls_namespace="test_api")
    api.add_router("/", router)
    return TestClient(api)


def test_conflict_renders_the_envelope(client):
    response = client.get("/conflict")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {
        "code": "stock_gone",
        "message": "Stok tükendi.",
        "fields": {"sku": ["Stokta yok."]},
    }


def test_validation_error_renders_the_envelope_with_turkish_message(client):
    response = client.post("/validate", json={"name": "x", "qty": "many"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "Girilen bilgiler geçersiz."
    assert list(body["fields"]) == ["qty"]
    assert body["fields"]["qty"]


def test_missing_body_field_is_keyed_by_field_name(client):
    response = client.post("/validate", json={"qty": 1})

    assert list(response.json()["fields"]) == ["name"]


def test_not_found_renders_the_envelope(client):
    response = client.get("/missing")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {
        "code": "not_found",
        "message": "Kayıt bulunamadı.",
        "fields": {},
    }


def test_unexpected_error_is_a_500_envelope_without_internals(client):
    response = client.get("/boom")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json() == {
        "code": "server_error",
        "message": "Beklenmeyen bir hata oluştu.",
        "fields": {},
    }


def test_money_is_a_two_decimal_string(client):
    assert client.get("/price").json() == {"price": "349.90"}


def test_pagination_envelope_and_defaults(client):
    Group.objects.bulk_create([Group(name=f"g{i:03}") for i in range(30)])

    body = client.get("/groups").json()

    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 30  # noqa: PLR2004
    assert body["page"] == 1
    assert body["page_size"] == 24  # noqa: PLR2004
    assert len(body["items"]) == 24  # noqa: PLR2004


def test_pagination_second_page(client):
    Group.objects.bulk_create([Group(name=f"g{i:03}") for i in range(30)])

    body = client.get("/groups?page=2").json()

    assert len(body["items"]) == 6  # noqa: PLR2004
    assert body["page"] == 2  # noqa: PLR2004


def test_page_size_above_100_is_rejected_with_the_envelope(client):
    response = client.get("/groups?page_size=101")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "page_size" in response.json()["fields"]


def test_query_filter_schema_has_q():
    assert QueryFilterSchema(q="gömlek").q == "gömlek"
    assert QueryFilterSchema().q is None


def test_ordering_allows_listed_fields_and_descending(client):
    Group.objects.bulk_create([Group(name="a"), Group(name="b")])

    assert client.get("/ordered?ordering=-name").json() == [
        {"name": "b"},
        {"name": "a"},
    ]


def test_ordering_outside_the_allowlist_is_a_422_envelope(client):
    response = client.get("/ordered?ordering=permissions")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "validation_error"
    assert "ordering" in response.json()["fields"]


def test_apply_ordering_raises_for_unknown_field():
    with pytest.raises(OrderingError):
        apply_ordering(Group.objects.all(), "-x", allowed={"name"})


def test_operations_without_an_explicit_operation_id_are_rejected():
    api = create_api(title="Strict", urls_namespace="strict_api")
    bad = Router()

    @bad.get("/x", response={200: dict})
    def x(request):
        return {}

    api.add_router("/", bad)

    with pytest.raises(ValueError, match="operation_id"):
        api.get_openapi_schema(path_prefix="/")


def test_429_keeps_its_status_and_turkish_message(client):
    response = client.get("/throttled")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["code"] == "rate_limited"
    assert "Çok fazla istek" in response.json()["message"]


def test_other_http_errors_keep_their_status(client):
    response = client.get("/teapot")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json() == {
        "code": "error",
        "message": "İstek işlenemedi.",
        "fields": {},
    }
