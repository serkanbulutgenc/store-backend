from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0010_legal_page_versions"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="storesettings",
            name="legal_versions",
        ),
    ]
