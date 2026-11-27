from django.db import migrations

# Second of two releases (store-infra#178): 0026 took the fields out of the models and
# kept the columns for the release before it. They have left the migration state, so
# only raw SQL can drop them. Reversing puts the columns back, empty.

DROP_COLUMNS = """
ALTER TABLE shop_storesettings
    DROP COLUMN bank_name,
    DROP COLUMN bank_account_holder,
    DROP COLUMN bank_iban,
    DROP COLUMN banner_image_url,
    DROP COLUMN banner_link,
    DROP COLUMN banner_alt,
    DROP COLUMN free_shipping_threshold
"""

ADD_COLUMNS = """
ALTER TABLE shop_storesettings
    ADD COLUMN bank_name varchar(100) NOT NULL DEFAULT '',
    ADD COLUMN bank_account_holder varchar(150) NOT NULL DEFAULT '',
    ADD COLUMN bank_iban varchar(34) NOT NULL DEFAULT '',
    ADD COLUMN banner_image_url varchar(500) NOT NULL DEFAULT '',
    ADD COLUMN banner_link varchar(500) NOT NULL DEFAULT '',
    ADD COLUMN banner_alt varchar(200) NOT NULL DEFAULT '',
    ADD COLUMN free_shipping_threshold numeric(10, 2) NULL
"""


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0026_retire_old_settings_columns"),
    ]

    operations = [
        migrations.RunSQL(DROP_COLUMNS, reverse_sql=ADD_COLUMNS),
    ]
