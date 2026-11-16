import django.db.models.deletion
from django.db import migrations, models

LEGAL_PAGES = (
    "distance_sales_contract",
    "pre_information_form",
    "returns_withdrawal",
    "privacy_notice",
    "delivery_shipping",
    "payment_methods",
    "about_contact",
)


def seed(apps, schema_editor):
    """Both Menus start out matching the Storefront as it was before Menus existed:
    the Header holds the Category tree, the Footer every Legal page plus Legal page
    search and Order tracking."""
    Menu = apps.get_model("shop", "Menu")
    MenuItem = apps.get_model("shop", "MenuItem")
    Category = apps.get_model("catalogue", "Category")
    CategoryProfile = apps.get_model("store_catalog", "CategoryProfile")
    header, _ = Menu.objects.get_or_create(key="header")
    footer, _ = Menu.objects.get_or_create(key="footer")
    if MenuItem.objects.filter(menu__in=[header, footer]).exists():
        return
    sort_order = dict(CategoryProfile.objects.values_list("category_id", "sort_order"))
    roots = sorted(
        Category.objects.filter(depth=1),
        key=lambda c: (sort_order.get(c.pk, 0), c.name.casefold()),
    )
    for position, root in enumerate(roots):
        MenuItem.objects.create(
            menu=header,
            position=position,
            target_type="category",
            category=root,
            include_subcategories=True,
        )
    for position, page in enumerate(LEGAL_PAGES):
        MenuItem.objects.create(
            menu=footer,
            position=position,
            target_type="legal_page",
            legal_page=page,
        )
    MenuItem.objects.create(
        menu=footer,
        position=len(LEGAL_PAGES),
        target_type="builtin",
        builtin="legal_search",
    )
    MenuItem.objects.create(
        menu=footer,
        position=len(LEGAL_PAGES) + 1,
        target_type="builtin",
        builtin="order_tracking",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("catalogue", "0032_category_exclude_from_menu_category_long_description"),
        ("store_catalog", "0007_category_profile"),
        ("shop", "0013_reviews"),
    ]

    operations = [
        migrations.CreateModel(
            name="Menu",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(choices=[("header", "Üst menü"), ("footer", "Alt menü")], max_length=20, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name="MenuItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=0)),
                ("target_type", models.CharField(choices=[("category", "Kategori"), ("legal_page", "Yasal sayfa"), ("builtin", "Hazır sayfa"), ("url", "Özel bağlantı"), ("group", "Grup başlığı")], max_length=20)),
                ("include_subcategories", models.BooleanField(default=False)),
                ("legal_page", models.CharField(blank=True, max_length=40)),
                ("builtin", models.CharField(blank=True, max_length=40)),
                ("url", models.CharField(blank=True, max_length=500)),
                ("label", models.CharField(blank=True, help_text="Overrides the target's own label; also names a group heading.", max_length=200)),
                ("is_hidden", models.BooleanField(default=False)),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="catalogue.category")),
                ("menu", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="shop.menu")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="shop.menuitem")),
            ],
            options={"ordering": ["position", "pk"]},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
