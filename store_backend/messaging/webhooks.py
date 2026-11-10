"""Bounce and complaint webhook, provider-neutral.

The relay is configured to POST JSON here with `Authorization: Bearer <secret>`:
`{"message_id": "<uuid>", "type": "bounce" | "complaint", "reason": "..."}`, or a list
of such objects. The relay vendor is not chosen yet; a thin forwarder (or the
provider's own template) maps its payload to this shape.
"""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus

from django.conf import settings
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from store_backend.services import outbox

EVENT_TYPES = {"bounce", "complaint"}


def _authorised(request) -> bool:
    secret = settings.EMAIL_WEBHOOK_SECRET
    if not secret:
        return False
    header = request.headers.get("Authorization", "")
    return hmac.compare_digest(header.encode(), f"Bearer {secret}".encode())


@csrf_exempt
@require_POST
def email_events(request) -> HttpResponse:
    if not _authorised(request):
        return HttpResponse(status=HTTPStatus.FORBIDDEN)
    try:
        body = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=HTTPStatus.BAD_REQUEST)
    events = body if isinstance(body, list) else [body]
    marked = 0
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
            continue
        reason = str(event.get("reason") or event["type"])
        if outbox.mark_bounced(str(event.get("message_id", "")), reason):
            marked += 1
    return JsonResponse({"marked": marked})
