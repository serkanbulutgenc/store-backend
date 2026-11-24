# ruff: noqa: F811
"""The Ana sayfa layout: per-user Widget list, role defaults, permission filtering."""

from http import HTTPStatus

import pytest
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission

from store_backend.staff.models import HomeLayout
from store_backend.staff.tests.conftest import api_for  # noqa: F401
from store_backend.staff.tests.conftest import owner  # noqa: F401
from store_backend.staff.tests.conftest import staff_user  # noqa: F401

pytestmark = pytest.mark.django_db

ALL = ["new_orders", "unpaid_transfers_near_expiry", "open_returns", "low_stock"]
HOME = "/api/admin/home"
LAYOUT = "/api/admin/home/layout"


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


@pytest.fixture
def boss(owner, api_for):
    return api_for(owner, enrol=True)


def test_a_user_who_never_configured_gets_the_default(staff):
    body = staff.get(HOME).json()
    assert body["layout"] == ALL
    assert body["available"] == ALL
    assert body["is_default"] is True
    assert all(key in body for key in ALL)


def test_saving_turns_widgets_off_and_stores_them_per_user(staff, staff_user, boss):
    response = staff.put(LAYOUT, {"widgets": []})
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["layout"] == []
    assert body["is_default"] is False
    assert "open_returns" not in body  # a Widget that is off is not queried
    assert staff.get(HOME).json()["layout"] == []
    other = boss.get(HOME).json()  # another user is unaffected
    assert other["layout"] == ALL


def test_turning_a_widget_back_on(staff):
    staff.put(LAYOUT, {"widgets": []})
    body = staff.put(LAYOUT, {"widgets": ["open_returns"]}).json()
    assert body["layout"] == ["open_returns"]
    assert body["open_returns"]["count"] == 0


def test_reset_returns_to_the_default(staff, staff_user):
    staff.put(LAYOUT, {"widgets": []})
    body = staff.delete(LAYOUT).json()
    assert body["is_default"] is True
    assert body["layout"] == ALL
    assert not HomeLayout.objects.filter(user=staff_user).exists()


def test_an_unknown_widget_is_refused(staff):
    response = staff.put(LAYOUT, {"widgets": ["nope"]})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "widget_not_available"


def test_a_duplicate_widget_is_refused(staff):
    response = staff.put(LAYOUT, {"widgets": ["open_returns", "open_returns"]})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "widget_duplicated"


def test_widgets_keep_the_order_the_user_chose(staff):
    order = ["low_stock", "open_returns", "new_orders"]
    body = staff.put(LAYOUT, {"widgets": order}).json()
    assert body["layout"] == order
    assert "unpaid_transfers_near_expiry" not in body  # off, so never queried


def test_each_widget_needs_its_own_permission(staff, staff_user):
    staff_user.groups.clear()
    perm = Permission.objects.get(
        content_type__app_label="staff",
        codename="view_products",
    )
    staff_user.user_permissions.add(perm)
    body = staff.get(HOME).json()
    assert body["layout"] == ["low_stock"]
    assert body["available"] == ["low_stock"]
    assert "new_orders" not in body
    refused = staff.put(LAYOUT, {"widgets": ["new_orders"]})
    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_widget_without_its_permission_is_refused_and_never_returned(
    staff,
    staff_user,
):
    staff_user.groups.clear()  # still staff, but holds no Dashboard permission
    group = Group.objects.create(name="Nothing")
    staff_user.groups.add(group)
    body = staff.get(HOME).json()
    assert body["layout"] == []
    assert body["available"] == []
    assert "open_returns" not in body
    response = staff.put(LAYOUT, {"widgets": ["open_returns"]})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_a_saved_widget_whose_permission_was_revoked_is_dropped(
    staff,
    staff_user,
):
    staff.put(LAYOUT, {"widgets": ["open_returns"]})
    staff_user.groups.clear()
    body = staff.get(HOME).json()
    assert body["layout"] == []
    assert "open_returns" not in body


def test_a_retired_key_in_a_saved_layout_is_dropped(staff, staff_user):
    HomeLayout.objects.create(user=staff_user, widgets=["retired", "open_returns"])
    assert staff.get(HOME).json()["layout"] == ["open_returns"]
