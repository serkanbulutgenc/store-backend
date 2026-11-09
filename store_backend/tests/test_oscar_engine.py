"""Oscar runs as a headless engine: models and services only, no dashboard, no URLs."""

from io import StringIO

import pytest
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.urls import Resolver404
from django.urls import get_resolver
from django.urls import resolve

from store_backend.users.models import User

OSCAR_PATHS = [
    "/catalogue/",
    "/basket/",
    "/checkout/",
    "/dashboard/",
    "/offers/",
    "/search/",
    "/accounts/login/oscar/",
]


def test_dashboard_apps_are_dropped_except_the_label_shim():
    dashboard_apps = [
        app for app in settings.INSTALLED_APPS if app.startswith("oscar.apps.dashboard")
    ]

    assert dashboard_apps == []


def test_app_starts_with_the_dashboard_label_provided_by_a_no_op_shim():
    config = apps.get_app_config("dashboard")

    assert config.name == "oscar.apps.dashboard"
    assert type(config).__module__ == "store_backend.oscar_shim"
    # No sub-dashboard app label may be registered.
    labels = {c.label for c in apps.get_app_configs()}
    assert not {label for label in labels if label.endswith("_dashboard")}


def test_oscar_models_use_the_email_user():
    basket = apps.get_model("basket", "Basket")

    assert settings.AUTH_USER_MODEL == "users.User"
    assert basket.objects.model is basket
    assert basket.owner.field.related_model is User


@pytest.mark.django_db
def test_migrations_are_in_sync_with_models():
    out = StringIO()

    call_command("makemigrations", "--check", "--dry-run", stdout=out)

    assert "No changes detected" in out.getvalue()


@pytest.mark.parametrize("path", OSCAR_PATHS)
def test_no_oscar_url_is_routed(path):
    with pytest.raises(Resolver404):
        resolve(path)


def test_no_oscar_url_namespaces_are_registered():
    namespaces = set(get_resolver().namespace_dict)

    assert not namespaces & {"catalogue", "basket", "checkout", "dashboard", "customer"}


def test_basket_middleware_runs_after_authentication():
    middleware = settings.MIDDLEWARE
    basket = middleware.index("oscar.apps.basket.middleware.BasketMiddleware")

    assert basket > middleware.index(
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    )


def test_haystack_uses_the_simple_backend():
    assert settings.HAYSTACK_CONNECTIONS["default"]["ENGINE"] == (
        "haystack.backends.simple_backend.SimpleEngine"
    )


def test_guest_checkout_is_enabled():
    assert settings.OSCAR_ALLOW_ANON_CHECKOUT is True


def test_frontend_origin_is_trusted():
    assert "http://localhost:3000" in settings.CSRF_TRUSTED_ORIGINS


def test_settings_can_be_rendered_without_oscar_urls():
    # Error reporters repr every setting; a lazy Oscar reverse would raise here.
    assert all(
        repr(getattr(settings, name)) for name in dir(settings) if name.isupper()
    )
