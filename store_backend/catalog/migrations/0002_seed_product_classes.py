"""Seed the fixed Product classes. KDV: 10% clothing, footwear, underwear; 20% default
(accessories are 20% until the accountant says otherwise, overridable per Product)."""

from django.db import migrations

CLASSES = [
    # name, size scheme, KDV %, hygiene
    ("Giyim", "letters", "10.00", False),
    ("Ayakkabı", "shoes", "10.00", False),
    ("İç Giyim", "letters", "10.00", True),
    ("Sütyen", "bra", "10.00", True),
    ("Aksesuar", "one_size", "20.00", False),
]


def seed(apps, schema_editor):
    ProductClass = apps.get_model("catalogue", "ProductClass")
    Profile = apps.get_model("store_catalog", "ProductClassProfile")
    for name, scheme, rate, hygiene in CLASSES:
        pc, _ = ProductClass.objects.get_or_create(
            name=name, defaults={"requires_shipping": True, "track_stock": True}
        )
        Profile.objects.get_or_create(
            product_class=pc,
            defaults={
                "size_scheme": scheme,
                "default_kdv_rate": rate,
                "is_hygiene": hygiene,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("store_catalog", "0001_initial"),
        ("catalogue", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
