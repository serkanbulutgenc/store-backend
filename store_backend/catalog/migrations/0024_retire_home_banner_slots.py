"""Turn the home Banner Slots into Homepage section images (store-infra#172).

The Slots `home_hero` and `home_mid` go away: their Banners become section images (the
`layout` Slot, everything else kept), and the layout, its pending change and every
approved version are rewritten to refer to them, so the Storefront shows what it showed
and an old version still validates.
"""

from django.db import migrations
from django.utils import timezone

HERO, MID, LAYOUT = "home_hero", "home_mid", "layout"
SLIDES = 24


def upgrade_sections(sections, hero_ids, mid_ids):
    """The sections in the new shape. `hero_ids` / `mid_ids` are the Banners the home
    page was drawing from, in order. An Afiş section left with no image is dropped."""
    upgraded = []
    for section in sections:
        section = dict(section)
        settings = dict(section.get("settings") or {})
        if section.get("type") == "slider":
            source = settings.get("source", "home")
            if source == "banners":
                settings["source"] = "own"
            elif source == "home" and hero_ids:
                settings["source"] = "own"
                settings["banner_ids"] = hero_ids[: settings.get("count", 5)]
        elif section.get("type") == "banner":
            settings.pop("slot", None)
            index = settings.pop("index", 0)
            if settings.get("banner_id") is None:
                settings.pop("banner_id", None)
                if index < len(mid_ids):
                    settings["banner_id"] = mid_ids[index]
            if settings.get("banner_id") is None:
                continue  # a strip with no image showed nothing before and has nothing now
        if settings:
            section["settings"] = settings
        else:
            section.pop("settings", None)
        upgraded.append(section)
    return upgraded


def retire_slots(apps, schema_editor):
    Banner = apps.get_model("store_catalog", "Banner")
    now = timezone.now()

    def drawn(slot):
        live = Banner.objects.filter(slot=slot, status="published", image_status="ready")
        live = live.exclude(ends_at__lte=now).order_by("position", "pk")
        return list(live.values_list("pk", flat=True))

    hero_ids, mid_ids = drawn(HERO), drawn(MID)
    Banner.objects.filter(slot__in=[HERO, MID]).update(slot=LAYOUT)

    ContentType = apps.get_model("contenttypes", "ContentType")
    kind = ContentType.objects.filter(
        app_label="store_catalog",
        model="homepagelayout",
    ).first()

    layout = apps.get_model("store_catalog", "HomepageLayout")
    for row in layout.objects.all():
        row.sections = upgrade_sections(row.sections, hero_ids, mid_ids)
        row.save(update_fields=["sections"])
    if kind is None:
        return
    for model_name in ("PendingChange", "ApprovedVersion"):
        model = apps.get_model("staff", model_name)
        for row in model.objects.filter(content_type=kind):
            if isinstance(row.payload, dict) and "sections" in row.payload:
                sections = upgrade_sections(row.payload["sections"], hero_ids, mid_ids)
                row.payload = {**row.payload, "sections": sections}
                row.save(update_fields=["payload"])


class Migration(migrations.Migration):

    dependencies = [
        ("store_catalog", "0023_banner_layout_slot"),
        ("staff", "0022_edit_homepage_permission"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(retire_slots, migrations.RunPython.noop)]
