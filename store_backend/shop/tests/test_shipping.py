"""Carrier rates: the dummy sheet, cheapest-carrier choice and province matching."""

from decimal import Decimal

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.services import shipping
from store_backend.shop.models import Carrier
from store_backend.shop.models import ProvinceZone
from store_backend.shop.models import RateRow
from store_backend.shop.models import Zone

pytestmark = pytest.mark.django_db

D = Decimal
BAND_2, BAND_5, BAND_10 = 2, 5, 10
PROVINCE_COUNT = 81
ZONE_COUNT = 4
CARRIER_COUNT = 3
RATE_ROWS = 12

# One province per zone, and the dummy sheet's cheapest (always PTT) per band.
PROVINCES = {1: "Mersin", 2: "Adana", 3: "İstanbul", 4: "Trabzon"}
PTT = {
    1: (45, 55, 70, 5),
    2: (55, 65, 85, 7),
    3: (65, 80, 105, 8),
    4: (75, 95, 125, 10),
}


def expected(zone: int, desi: int) -> Decimal:
    low, mid, high, extra = PTT[zone]
    if desi <= BAND_2:
        return D(low)
    if desi <= BAND_5:
        return D(mid)
    if desi <= BAND_10:
        return D(high)
    return D(high + extra * (desi - BAND_10))


@pytest.mark.parametrize("zone", [1, 2, 3, 4])
@pytest.mark.parametrize("desi", [1, 2, 3, 5, 6, 10, 11, 14])
def test_cheapest_carrier_at_every_band_edge_and_zone(zone, desi):
    result = shipping.fee_for(desi, PROVINCES[zone])
    assert result is not None
    assert result.fee == expected(zone, desi)
    assert result.carrier == "PTT"


def test_cheapest_carrier_changes_with_the_rate_table():
    ptt = Carrier.objects.get(name="PTT")
    RateRow.objects.filter(carrier=ptt).update(band_0_2=D("999"))
    result = shipping.fee_for(2, "Mersin")
    assert (result.carrier, result.fee) == ("Aras", D("52"))


def test_tie_goes_to_the_first_carrier_by_position():
    RateRow.objects.filter(zone__number=1).update(band_0_2=D("50"))
    assert shipping.fee_for(1, "Mersin").carrier == "Yurtiçi"


@pytest.mark.parametrize(
    "name",
    ["Şanlıurfa", "ŞANLIURFA", "şanlıurfa", "sanliurfa", " Şanlıurfa ", "SANLIURFA"],
)
def test_province_matches_regardless_of_case_and_marks(name):
    assert shipping.fee_for(3, name) is not None


@pytest.mark.parametrize(
    "name",
    ["Iğdır", "IĞDIR", "iğdir", "İğdır", "ığdır", "igdir", "IGDIR"],
)
def test_dotted_and_dotless_i_are_the_same_letter(name):
    result = shipping.fee_for(3, name)
    assert result is not None
    assert result.fee == expected(4, 3)


def test_unknown_province_or_desi_gives_none():
    assert shipping.fee_for(3, "Atlantis") is None
    assert shipping.fee_for(3, "") is None
    assert shipping.fee_for(3, None) is None
    assert shipping.fee_for(None, "Mersin") is None
    assert shipping.fee_for(0, "Mersin") is None
    assert shipping.fee_for(-1, "Mersin") is None


def test_zone_without_rates_gives_none():
    RateRow.objects.filter(zone__number=4).delete()
    assert shipping.fee_for(3, "Trabzon") is None


def test_all_81_provinces_are_mapped_into_four_zones():
    assert ProvinceZone.objects.count() == PROVINCE_COUNT
    assert Zone.objects.count() == ZONE_COUNT
    counts = {z.number: z.provinces.count() for z in Zone.objects.all()}
    assert counts == {1: 1, 2: 9, 3: 31, 4: 40}


def test_the_dummy_sheet_has_a_row_per_carrier_and_zone():
    assert Carrier.objects.count() == CARRIER_COUNT
    assert RateRow.objects.count() == RATE_ROWS
    row = RateRow.objects.get(carrier__name="Aras", zone__number=4)
    assert (row.band_0_2, row.band_3_5, row.band_6_10, row.per_extra_desi) == (
        D("95"),
        D("114"),
        D("142"),
        D("11"),
    )


def test_every_product_class_has_a_default_desi():
    profiles = {pc.name: pc.profile.default_desi for pc in ProductClass.objects.all()}
    assert profiles == {
        "Giyim": 2,
        "Ayakkabı": 4,
        "İç Giyim": 1,
        "Sütyen": 1,
        "Aksesuar": 1,
    }


def test_adding_or_renaming_a_province_keeps_its_lookup_key():
    zone = Zone.objects.get(number=2)
    row = ProvinceZone.objects.create(name="Yeni İl", zone=zone)
    assert row.key == "yeni il"
    assert shipping.fee_for(3, "YENİ İL").carrier == "PTT"
    row.name = "Başka"
    row.save()
    assert shipping.fee_for(3, "yeni il") is None
    assert shipping.fee_for(3, "baska") is not None
