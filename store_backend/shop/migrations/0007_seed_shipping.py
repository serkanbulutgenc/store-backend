"""Seed the dummy rate sheet (placeholder numbers, not quotes), the 81 province
zones and a default desi per Product class. Real contract prices replace the sheet
before launch."""

import unicodedata

from django.db import migrations

ZONES = {
    1: ["Mersin"],
    2: [
    "Adana",
    "Hatay",
    "Osmaniye",
    "Karaman",
    "Niğde",
    "Konya",
    "Antalya",
    "Gaziantep",
    "Kahramanmaraş",
    ],
    3: [
    "Ankara",
    "Eskişehir",
    "Kayseri",
    "Kırıkkale",
    "Kırşehir",
    "Nevşehir",
    "Sivas",
    "Aksaray",
    "Çankırı",
    "Yozgat",
    "İzmir",
    "Aydın",
    "Denizli",
    "Manisa",
    "Muğla",
    "Uşak",
    "Afyonkarahisar",
    "Kütahya",
    "Burdur",
    "Isparta",
    "İstanbul",
    "Edirne",
    "Kırklareli",
    "Tekirdağ",
    "Çanakkale",
    "Balıkesir",
    "Bursa",
    "Bilecik",
    "Kocaeli",
    "Sakarya",
    "Yalova",
    ],
    4: [
    "Zonguldak",
    "Bartın",
    "Karabük",
    "Kastamonu",
    "Sinop",
    "Samsun",
    "Çorum",
    "Amasya",
    "Tokat",
    "Ordu",
    "Giresun",
    "Gümüşhane",
    "Trabzon",
    "Rize",
    "Artvin",
    "Bayburt",
    "Düzce",
    "Bolu",
    "Erzurum",
    "Erzincan",
    "Bingöl",
    "Bitlis",
    "Elazığ",
    "Hakkari",
    "Iğdır",
    "Kars",
    "Malatya",
    "Muş",
    "Tunceli",
    "Van",
    "Ağrı",
    "Ardahan",
    "Adıyaman",
    "Batman",
    "Diyarbakır",
    "Mardin",
    "Siirt",
    "Şanlıurfa",
    "Şırnak",
    "Kilis",
    ],
}

# carrier, zone -> 0-2, 3-5, 6-10, each extra desi over 10 (KDV-inclusive TRY)
RATES = {
    "Yurtiçi": {
        1: (55, 65, 80, 6),
        2: (70, 80, 100, 8),
        3: (85, 100, 125, 10),
        4: (100, 120, 150, 12),
    },
    "Aras": {
        1: (52, 62, 76, 6),
        2: (66, 76, 95, 8),
        3: (80, 95, 118, 10),
        4: (95, 114, 142, 11),
    },
    "PTT": {
        1: (45, 55, 70, 5),
        2: (55, 65, 85, 7),
        3: (65, 80, 105, 8),
        4: (75, 95, 125, 10),
    },
}

DEFAULT_DESI = {"Giyim": 2, "Ayakkabı": 4, "İç Giyim": 1, "Sütyen": 1, "Aksesuar": 1}


def fold(name):
    """Same folding as services.shipping.province_key, frozen here."""
    text = name.replace("İ", "i").replace("I", "i").replace("ı", "i").casefold()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def seed(apps, schema_editor):
    Zone = apps.get_model("shop", "Zone")
    ProvinceZone = apps.get_model("shop", "ProvinceZone")
    Carrier = apps.get_model("shop", "Carrier")
    RateRow = apps.get_model("shop", "RateRow")
    ProductClassProfile = apps.get_model("store_catalog", "ProductClassProfile")

    zones = {n: Zone.objects.get_or_create(number=n)[0] for n in ZONES}
    for number, names in ZONES.items():
        for name in names:
            ProvinceZone.objects.get_or_create(
                name=name, defaults={"key": fold(name), "zone": zones[number]}
            )
    for position, (name, by_zone) in enumerate(RATES.items()):
        carrier, _ = Carrier.objects.get_or_create(
            name=name, defaults={"position": position}
        )
        for number, (a, b, c, extra) in by_zone.items():
            RateRow.objects.get_or_create(
                carrier=carrier,
                zone=zones[number],
                defaults={
                    "band_0_2": a,
                    "band_3_5": b,
                    "band_6_10": c,
                    "per_extra_desi": extra,
                },
            )
    for class_name, desi in DEFAULT_DESI.items():
        ProductClassProfile.objects.filter(product_class__name=class_name).update(
            default_desi=desi
        )


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0006_carrier_zone_provincezone_raterow"),
        ("store_catalog", "0005_productclassprofile_default_desi_productprofile_desi"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
