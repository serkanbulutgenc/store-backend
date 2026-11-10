"""Error tracking with PII scrubbing, and JSON log formatting.

Shoppers' emails, phone numbers and addresses must never reach the hosted tracker. The
rule is deny by default: request bodies, cookies, headers and user records are dropped
outright, and any email or phone-looking text left in messages, exception values,
breadcrumbs or extra data is replaced. `init_sentry` does nothing without a DSN.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# +90 5xx xxx xx xx, 05xx..., or any run of 10+ digits with common separators.
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
DROPPED_REQUEST_KEYS = ("data", "cookies", "headers", "query_string", "env")
REDACTED = "[redacted]"


def scrub_text(text: str) -> str:
    return PHONE.sub(REDACTED, EMAIL.sub(REDACTED, text))


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    event.pop("user", None)
    request = event.get("request")
    if request:
        for key in DROPPED_REQUEST_KEYS:
            request.pop(key, None)
    event.pop("server_name", None)
    # Everything else is scrubbed too (threads, stacktraces, ...), not a list of keys.
    return _scrub(event)


def init_sentry(dsn: str, environment: str, release: str = "") -> None:
    if not dsn:
        return
    try:
        _init(dsn, environment, release)
    except Exception:  # a bad DSN must not stop the shop from starting
        logging.getLogger(__name__).exception(
            "Sentry is disabled: invalid configuration",
        )


def _init(dsn: str, environment: str, release: str) -> None:
    import sentry_sdk  # noqa: PLC0415
    from sentry_sdk.integrations.celery import CeleryIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.django import DjangoIntegration  # noqa: PLC0415

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        send_default_pii=False,
        include_local_variables=False,  # locals hold form data, addresses, tokens
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=before_send,
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line on stdout, for `docker compose logs` and grep/jq."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.stack_info:
            entry["stack"] = record.stack_info
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)
