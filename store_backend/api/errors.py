"""One error envelope for both APIs: `{code, message, fields}`.

`message` is Turkish and shown to the shopper or staff member; `code` is a stable
machine-readable string; `fields` maps a field name to a list of messages.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied
from django.http import Http404
from ninja.errors import HttpError
from ninja.errors import ValidationError

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse
    from ninja import NinjaAPI

logger = logging.getLogger(__name__)

# status -> (code, Turkish message)
DEFAULTS: dict[int, tuple[str, str]] = {
    HTTPStatus.BAD_REQUEST: ("bad_request", "Geçersiz istek."),
    HTTPStatus.UNAUTHORIZED: ("unauthorized", "Oturum açmanız gerekiyor."),
    HTTPStatus.FORBIDDEN: ("forbidden", "Bu işlem için yetkiniz yok."),
    HTTPStatus.NOT_FOUND: ("not_found", "Kayıt bulunamadı."),
    HTTPStatus.CONFLICT: ("conflict", "İşlem mevcut durumla çakışıyor."),
    HTTPStatus.UNPROCESSABLE_ENTITY: (
        "validation_error",
        "Girilen bilgiler geçersiz.",
    ),
    HTTPStatus.TOO_MANY_REQUESTS: (
        "rate_limited",
        "Çok fazla istek gönderdiniz. Lütfen biraz sonra tekrar deneyin.",
    ),
    HTTPStatus.INTERNAL_SERVER_ERROR: (
        "server_error",
        "Beklenmeyen bir hata oluştu.",
    ),
}


class ApiError(Exception):
    """An expected failure that maps to the error envelope.

    Services raise a subclass; the handler renders it with `status`.
    """

    status: int = HTTPStatus.BAD_REQUEST

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        fields: dict[str, list[str]] | None = None,
    ) -> None:
        default_code, default_message = DEFAULTS[self.status]
        self.code = code or default_code
        self.message = message or default_message
        self.fields = fields or {}
        super().__init__(self.message)


class BadRequestError(ApiError):
    status = HTTPStatus.BAD_REQUEST


class UnauthorizedError(ApiError):
    status = HTTPStatus.UNAUTHORIZED


class ForbiddenError(ApiError):
    status = HTTPStatus.FORBIDDEN


class NotFoundError(ApiError):
    status = HTTPStatus.NOT_FOUND


class ConflictError(ApiError):
    """A state conflict, such as stock gone mid-checkout."""

    status = HTTPStatus.CONFLICT


class UnprocessableError(ApiError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY


def envelope(
    status: int,
    *,
    code: str | None = None,
    message: str | None = None,
    fields: dict[str, list[str]] | None = None,
) -> dict:
    default_code, default_message = DEFAULTS.get(
        status,
        ("error", "İstek işlenemedi."),
    )
    return {
        "code": code or default_code,
        "message": message or default_message,
        "fields": fields or {},
    }


def _field_name(loc: list | tuple) -> str:
    """Field name from a pydantic location: `("body", "data", "qty")` is `qty`."""
    parts = [str(p) for p in loc]
    # Drop the source ("body", "query", ...) and, for bodies, the parameter name.
    drop = 2 if parts[:1] == ["body"] and len(parts) > 2 else 1  # noqa: PLR2004
    return ".".join(parts[drop:]) or ".".join(parts)


def install_error_handlers(api: NinjaAPI) -> None:
    def respond(request: HttpRequest, status: int, **kwargs) -> HttpResponse:
        return api.create_response(request, envelope(status, **kwargs), status=status)

    @api.exception_handler(ApiError)
    def api_error(request: HttpRequest, exc: ApiError) -> HttpResponse:
        return respond(
            request,
            exc.status,
            code=exc.code,
            message=exc.message,
            fields=exc.fields,
        )

    @api.exception_handler(ValidationError)
    def validation(request: HttpRequest, exc: ValidationError) -> HttpResponse:
        fields: dict[str, list[str]] = {}
        for error in exc.errors:
            fields.setdefault(_field_name(error.get("loc", ())), []).append(
                str(error.get("msg", "")),
            )
        return respond(request, HTTPStatus.UNPROCESSABLE_ENTITY, fields=fields)

    @api.exception_handler(HttpError)
    def http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
        # Ninja's own messages are English; use ours.
        return respond(request, exc.status_code)

    @api.exception_handler(Http404)
    def not_found(request: HttpRequest, exc: Http404) -> HttpResponse:
        return respond(request, HTTPStatus.NOT_FOUND)

    @api.exception_handler(PermissionDenied)
    def forbidden(request: HttpRequest, exc: PermissionDenied) -> HttpResponse:
        return respond(request, HTTPStatus.FORBIDDEN)

    @api.exception_handler(Exception)
    def unexpected(request: HttpRequest, exc: Exception) -> HttpResponse:
        # Never leak internals; the traceback goes to the log (and error tracking).
        logger.exception("Unhandled error in %s", request.path, exc_info=exc)
        return respond(request, HTTPStatus.INTERNAL_SERVER_ERROR)
