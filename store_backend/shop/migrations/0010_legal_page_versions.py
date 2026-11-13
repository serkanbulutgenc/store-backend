"""Legal pages become rows: one immutable version per edit.

The text so far lived as Markdown in the frontend repo, with the current version of
each page held in `StoreSettings.legal_versions`. This migration copies that text into
`store_backend/shop/legal_seed/` rows at exactly the version numbers in force, so no
Consent record or open checkout is disturbed, and 0011 then drops the old field.
"""

from pathlib import Path

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

SEED_DIR = Path(__file__).resolve().parent.parent / "legal_seed"

# page key -> title when the text was moved (the page's address is fixed in the model).
TITLES = {
    "distance_sales_contract": "Mesafeli Satış Sözleşmesi",
    "pre_information_form": "Ön Bilgilendirme Formu",
    "returns_withdrawal": "İade ve Cayma Koşulları",
    "privacy_notice": "Gizlilik ve KVKK Aydınlatma Metni",
    "delivery_shipping": "Teslimat ve Kargo",
    "payment_methods": "Ödeme Yöntemleri",
    "about_contact": "Hakkımızda / İletişim",
}


def seed(apps, schema_editor):
    StoreSettings = apps.get_model("shop", "StoreSettings")
    LegalPageVersion = apps.get_model("shop", "LegalPageVersion")
    row = StoreSettings.objects.filter(pk=1).first()
    current = row.legal_versions if row else {}
    for page, title in TITLES.items():
        try:
            version = int(current.get(page, "1"))
        except (TypeError, ValueError):
            version = 1
        LegalPageVersion.objects.get_or_create(
            page=page,
            version=version,
            defaults={
                "title": title,
                "body": (SEED_DIR / f"{page}.md").read_text(encoding="utf-8"),
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("shop", "LegalPageVersion").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0009_return_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LegalPageVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("page", models.CharField(choices=[("distance_sales_contract", "distance_sales_contract"), ("pre_information_form", "pre_information_form"), ("returns_withdrawal", "returns_withdrawal"), ("privacy_notice", "privacy_notice"), ("delivery_shipping", "delivery_shipping"), ("payment_methods", "payment_methods"), ("about_contact", "about_contact")], max_length=40)),
                ("version", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, help_text="Empty for the text carried over from the first release.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["page", "-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="legalpageversion",
            constraint=models.UniqueConstraint(fields=("page", "version"), name="legal_page_version_unique"),
        ),
        migrations.RunPython(seed, unseed),
    ]
