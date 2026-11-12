from django.contrib.auth.models import Group

from store_backend.staff.permissions import OWNER_ONLY
from store_backend.staff.permissions import Perm


def codenames(group_name: str) -> set[str]:
    group = Group.objects.get(name=group_name)
    return set(
        group.permissions.filter(content_type__app_label="staff").values_list(
            "codename",
            flat=True,
        ),
    )


def test_owner_and_staff_groups_are_seeded_by_migration(db):
    assert Group.objects.filter(name__in=["Owner", "Staff"]).count() == 2  # noqa: PLR2004


def test_owner_has_every_named_permission(db):
    assert codenames("Owner") == {p.value for p in Perm}


def test_staff_has_everything_except_owner_only(db):
    assert codenames("Staff") == {p.value for p in Perm} - {p.value for p in OWNER_ONLY}


def test_owner_only_permissions_match_the_prd(db):
    assert {p.name for p in OWNER_ONLY} == {
        "MANAGE_SETTINGS",
        "MANAGE_STAFF_USERS",
        "MANAGE_COLOURS",
        "VIEW_COST_PRICES",
        "MANAGE_SUPPLIERS",
        "ARCHIVE_PRODUCTS",
        "RECORD_REFUNDS",
        "CANCEL_PAID_ORDERS",
        "MANAGE_ACCOUNT_DELETIONS",
        "EXPORT_ACCOUNTANT_CSV",
        "VIEW_AUDIT_LOG",
        "IMPORT_CATALOGUE",
    }


def test_marking_paid_is_a_staff_permission(db):
    assert Perm.MARK_PAID.value in codenames("Staff")
