# ruff: noqa: E501
import json
import logging
import sys

from store_backend import observability as obs


def test_text_scrubbing_removes_emails_and_phones():
    out = obs.scrub_text(
        "mail ayse.k+x@example.com tel +90 532 123 45 67 / 05321234567 ok order 1000123",
    )
    assert "@" not in out
    assert "532" not in out
    assert "05321234567" not in out
    assert "order 1000123" in out


def test_before_send_drops_request_body_cookies_user_and_scrubs_messages():
    event = {
        "message": "failed for ayse@example.com",
        "user": {"email": "ayse@example.com", "ip_address": "1.2.3.4"},
        "request": {
            "url": "https://shop.example/api/x?email=a@b.com",
            "data": {"phone": "05321234567"},
            "cookies": {"store_session": "secret"},
            "headers": {"Authorization": "Bearer t"},
            "query_string": "email=a@b.com",
            "method": "POST",
        },
        "exception": {
            "values": [{"type": "ValueError", "value": "bad address for 05321234567"}],
        },
        "breadcrumbs": {"values": [{"message": "to ayse@example.com"}]},
        "extra": {"note": "call +90 532 123 45 67"},
    }
    sent = json.dumps(obs.before_send(event, {}))
    for leak in ("ayse", "a@b.com", "532", "secret", "Bearer", "1.2.3.4"):
        assert leak not in sent
    assert "ValueError" in sent
    assert "POST" in sent


def test_init_sentry_without_dsn_is_a_noop():
    assert obs.init_sentry("", "production") is None


def test_json_formatter_one_object_with_exception():
    try:
        msg = "boom"
        raise RuntimeError(msg)  # noqa: TRY301
    except RuntimeError:
        record = logging.LogRecord(
            "x",
            logging.ERROR,
            __file__,
            1,
            "hello %s",
            ("w",),
            sys.exc_info(),
        )
    entry = json.loads(obs.JsonFormatter().format(record))
    assert entry["message"] == "hello w"
    assert entry["level"] == "ERROR"
    assert "RuntimeError: boom" in entry["exception"]


def test_before_send_scrubs_unlisted_fields_and_keeps_url_valid():
    event = {
        "threads": {"values": [{"name": "ayse@example.com"}]},
        "request": {"method": "GET"},
    }
    out = obs.before_send(event, {})
    assert "ayse" not in json.dumps(out)
    assert "url" not in out["request"]


def test_init_sentry_with_bad_dsn_does_not_raise():
    obs.init_sentry("not a dsn", "production")
