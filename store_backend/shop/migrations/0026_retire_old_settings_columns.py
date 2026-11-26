from django.db import migrations
from django.db import models

# First of two releases (store-infra#178). The fields leave the models but their columns
# stay, because the previous release still selects them. The NOT NULL ones get database
# defaults, as new code no longer fills them. The release after this one drops all seven
# columns with `RunSQL`: they are no longer in the migration state.

TEXT_COLUMNS = {
    "bank_name": 100,
    "bank_account_holder": 150,
    "bank_iban": 34,
    "banner_image_url": 500,
    "banner_link": 500,
    "banner_alt": 200,
}


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0025_basket_campaigns_per_order"),
    ]

    operations = [
        *[
            migrations.AlterField(
                model_name="storesettings",
                name=name,
                field=models.CharField(blank=True, db_default="", max_length=length),
            )
            for name, length in TEXT_COLUMNS.items()
        ],
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="storesettings", name=name)
                for name in [*TEXT_COLUMNS, "free_shipping_threshold"]
            ],
        ),
    ]
