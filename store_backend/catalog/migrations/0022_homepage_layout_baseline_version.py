from django.db import migrations


def keep_current_layout(apps, schema_editor):
    """The live layout is version 1 of its history, so the first restore has a way back."""
    layout = apps.get_model("store_catalog", "HomepageLayout").objects.filter(pk=1).first()
    if layout is None:
        return
    content_type, _ = apps.get_model("contenttypes", "ContentType").objects.get_or_create(
        app_label="store_catalog",
        model="homepagelayout",
    )
    version = apps.get_model("staff", "ApprovedVersion")
    if version.objects.filter(content_type=content_type, object_id=1).exists():
        return
    version.objects.create(
        content_type=content_type,
        object_id=1,
        version=1,
        payload={"sections": layout.sections},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("store_catalog", "0021_homepage_approval"),
        ("staff", "0022_edit_homepage_permission"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(keep_current_layout, migrations.RunPython.noop)]
