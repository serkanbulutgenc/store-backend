"""Owner-only Dashboard endpoints for the carrier rate table, zones and desi."""

from decimal import Decimal
from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.services import shipping
from store_backend.shop.models import ProvinceZone
from store_backend.shop.models import RateRow
from store_backend.shop.models import Zone
from store_backend.staff.models import AuditEntry

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("computed_shipping")]

URL = "/api/admin/shipping"


@pytest.fixture
def owner_api(owner, api_for):
    return api_for(owner, enrol=True)


def test_staff_and_anonymous_are_refused(staff_user, api_for, client):
    api = api_for(staff_user)
    row = RateRow.objects.first()
    assert api.get(URL).status_code == HTTPStatus.FORBIDDEN
    patch = api.patch(f"{URL}/rates/{row.pk}", {"band_0_2": "1"})
    assert patch.status_code == HTTPStatus.FORBIDDEN
    assert client.get(URL).status_code == HTTPStatus.UNAUTHORIZED


def test_the_owner_reads_the_whole_table(owner_api):
    body = owner_api.get(URL).json()

    assert [c["name"] for c in body["carriers"]] == ["Yurtiçi", "Aras", "PTT"]
    assert [z["number"] for z in body["zones"]] == [1, 2, 3, 4]
    assert len(body["rates"]) == 12  # noqa: PLR2004
    assert len(body["provinces"]) == 81  # noqa: PLR2004
    rate = body["rates"][0]
    assert set(rate) == {
        "id", "carrier_id", "zone_id",
        "band_0_2", "band_3_5", "band_6_10", "per_extra_desi",
    }  # fmt: skip


def test_changing_a_price_changes_the_computed_fee(owner_api):
    ptt_zone_1 = RateRow.objects.get(carrier__name="PTT", zone__number=1)

    response = owner_api.patch(
        f"{URL}/rates/{ptt_zone_1.pk}",
        {"band_0_2": "53.50"},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["band_0_2"] == "53.50"
    result = shipping.fee_for(1, "Mersin")
    assert (result.carrier, result.fee) == ("Aras", Decimal("52.00"))
    entry = AuditEntry.objects.latest("pk")
    assert entry.before["band_0_2"] == "45.00"
    assert entry.after["band_0_2"] == "53.50"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"band_0_2": "0"}, "band_0_2"),
        ({"band_0_2": "-5"}, "band_0_2"),
        ({"band_3_5": "abc"}, "band_3_5"),
        ({"per_extra_desi": "0"}, "per_extra_desi"),
        ({"band_0_2": "70"}, "band_0_2"),  # above the 3-5 band (55)
        ({"band_6_10": "50"}, "band_6_10"),  # below the 3-5 band (55)
    ],
)
def test_bad_prices_are_refused(owner_api, changes, field):
    row = RateRow.objects.get(carrier__name="PTT", zone__number=1)

    response = owner_api.patch(f"{URL}/rates/{row.pk}", changes)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert field in response.json()["fields"]
    row.refresh_from_db()
    assert row.band_0_2 == Decimal(45)


def test_moving_a_province_to_another_zone(owner_api):
    mersin = ProvinceZone.objects.get(name="Mersin")
    zone_4 = Zone.objects.get(number=4)

    response = owner_api.patch(
        f"{URL}/provinces/{mersin.pk}",
        {"zone_id": zone_4.pk},
    )

    assert response.status_code == HTTPStatus.OK, response.content
    assert shipping.fee_for(1, "mersin").fee == Decimal(75)
    assert AuditEntry.objects.latest("pk").after["zone"] == 4  # noqa: PLR2004


def test_an_unknown_zone_is_refused(owner_api):
    mersin = ProvinceZone.objects.get(name="Mersin")

    response = owner_api.patch(f"{URL}/provinces/{mersin.pk}", {"zone_id": 9999})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_renaming_a_zone(owner_api):
    zone = Zone.objects.get(number=2)

    response = owner_api.patch(f"{URL}/zones/{zone.pk}", {"name": "Yakın"})

    assert response.status_code == HTTPStatus.OK
    zone.refresh_from_db()
    assert zone.name == "Yakın"


def test_unknown_ids_are_404(owner_api):
    assert owner_api.patch(f"{URL}/rates/99999", {"band_0_2": "1"}).status_code == (
        HTTPStatus.NOT_FOUND
    )


def test_class_default_desi_is_listed_and_editable_by_the_owner(
    owner_api,
    staff_user,
    api_for,
):
    giyim = ProductClass.objects.get(name="Giyim")
    classes = owner_api.get("/api/admin/product-classes").json()
    assert {c["name"]: c["default_desi"] for c in classes}["Giyim"] == 2  # noqa: PLR2004

    response = owner_api.patch(
        f"/api/admin/product-classes/{giyim.pk}",
        {"default_desi": 3},
    )
    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["default_desi"] == 3  # noqa: PLR2004

    staff = api_for(staff_user)
    refused = staff.patch(f"/api/admin/product-classes/{giyim.pk}", {"default_desi": 5})
    assert refused.status_code == HTTPStatus.FORBIDDEN
    bad = owner_api.patch(f"/api/admin/product-classes/{giyim.pk}", {"default_desi": 0})
    assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
