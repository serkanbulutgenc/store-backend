import pytest
from django.test import Client

from store_backend.staff.models import AuditEntry
from store_backend.staff.permissions import omit_owner_only

pytestmark = pytest.mark.django_db


def test_staff_login_is_audited(staff_user, log_in):
    log_in(Client(), staff_user)

    entry = AuditEntry.objects.get(action="login")
    assert entry.actor == staff_user


def test_customer_login_is_not_audited(customer, log_in):
    log_in(Client(), customer)

    assert not AuditEntry.objects.filter(action="login").exists()


def test_owner_only_fields_are_omitted_for_staff(staff_user):
    payload = {"name": "Gömlek", "cost_price": "120.00", "price": "349.90"}

    assert omit_owner_only(staff_user, payload, "cost_price") == {
        "name": "Gömlek",
        "price": "349.90",
    }


def test_owner_only_fields_are_kept_for_the_owner(owner):
    payload = {"name": "Gömlek", "cost_price": "120.00"}

    assert omit_owner_only(owner, payload, "cost_price") == payload
