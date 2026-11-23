from datetime import timedelta

import pytest
from django.utils import timezone

from store_backend.services import stock_alerts
from store_backend.shop.models import AlertConsent
from store_backend.shop.models import StockAlert
from store_backend.shop.tasks import purge_stock_alerts

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def alert(variant, email, *, confirmed_at=None, expires_at):
    return StockAlert.objects.create(
        email=email,
        variant=variant,
        notice_version="1",
        confirm_token=f"t-{email}",
        confirmed_at=confirmed_at,
        expires_at=expires_at,
    )


def consent(variant, email, *, age):
    return AlertConsent.objects.create(
        email=email,
        variant=variant,
        consented_at=NOW - age,
        notice_version="1",
        how_given=AlertConsent.HowGiven.CONFIRM_LINK,
    )


def test_unconfirmed_rows_go_after_48_hours_and_not_before(shop):
    variant = shop[1]["L"]
    alert(variant, "late@example.com", expires_at=NOW - timedelta(minutes=1))
    alert(variant, "fresh@example.com", expires_at=NOW + timedelta(hours=1))

    result = stock_alerts.purge(NOW)

    assert result["alerts"] == 1
    assert StockAlert.objects.get().email == "fresh@example.com"


def test_confirmed_rows_go_after_90_days(shop):
    variant = shop[1]["L"]
    start = NOW - timedelta(days=90, minutes=1)
    alert(
        variant,
        "old@example.com",
        confirmed_at=start,
        expires_at=start + stock_alerts.ALERT_TTL,
    )
    recent = NOW - timedelta(days=89)
    alert(
        variant,
        "recent@example.com",
        confirmed_at=recent,
        expires_at=recent + stock_alerts.ALERT_TTL,
    )

    stock_alerts.purge(NOW)

    assert StockAlert.objects.get().email == "recent@example.com"


def test_consent_records_are_kept_three_years(shop):
    variant = shop[1]["L"]
    consent(variant, "old@example.com", age=timedelta(days=365 * 3, minutes=1))
    consent(variant, "recent@example.com", age=timedelta(days=365 * 3 - 1))

    result = stock_alerts.purge(NOW)

    assert result["consents"] == 1
    assert AlertConsent.objects.get().email == "recent@example.com"


def test_purge_is_idempotent_and_the_task_runs_it(shop):
    variant = shop[1]["L"]
    alert(variant, "late@example.com", expires_at=NOW - timedelta(days=1))
    consent(variant, "old@example.com", age=timedelta(days=365 * 4))

    first = purge_stock_alerts()
    second = purge_stock_alerts()

    assert first == {"alerts": 1, "consents": 1}
    assert second == {"alerts": 0, "consents": 0}


def test_the_purge_is_on_the_beat_schedule(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["purge-stock-alerts"]

    assert entry["task"] == "store_backend.shop.tasks.purge_stock_alerts"
