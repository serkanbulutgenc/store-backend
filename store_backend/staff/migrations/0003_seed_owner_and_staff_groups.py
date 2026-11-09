"""Seed the two fixed groups. The permission lists are frozen here on purpose: a later
permission needs its own migration that adds it to the groups.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

SHARED = [
    "view_orders",
    "manage_orders",
    "mark_paid",
    "view_products",
    "manage_products",
    "manage_product_photos",
    "adjust_stock",
    "manage_categories",
    "manage_brands",
    "view_customers",
    "manage_invoices",
]
OWNER_ONLY = [
    "manage_settings",
    "manage_staff_users",
    "manage_colours",
    "view_cost_prices",
    "manage_suppliers",
    "archive_products",
    "record_refunds",
    "cancel_paid_orders",
    "manage_account_deletions",
    "export_accountant_csv",
    "view_audit_log",
]


def seed(apps, schema_editor):
    # Permissions are normally created after all migrations (post_migrate); create
    # this app's now so the groups can reference them.
    app_config = apps.get_app_config("staff")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    perms = {
        p.codename: p
        for p in Permission.objects.filter(content_type__app_label="staff")
    }
    for name, codenames in (("Owner", SHARED + OWNER_ONLY), ("Staff", SHARED)):
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set([perms[c] for c in codenames])


def unseed(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=["Owner", "Staff"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0002_auditentry_append_only"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
