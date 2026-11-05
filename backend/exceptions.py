"""
One error format for the whole REST API:

    {"error": {"code": "not_found", "message": "Not found.", "details": {...}}}

Unexpected errors return a generic message; the technical details are logged
on the server only.
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details=None) -> Response:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return Response(body, status=http_status)


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)

    response = exception_handler(exc, context)

    if response is None:
        logger.exception("Unhandled API error", exc_info=exc)
        return _error("server_error", "An unexpected error occurred.", status.HTTP_500_INTERNAL_SERVER_ERROR)

    if isinstance(exc, exceptions.ValidationError):
        return _error("invalid", "The submitted data is not valid.", response.status_code, response.data)

    detail = response.data.get("detail", "") if isinstance(response.data, dict) else ""
    code = getattr(detail, "code", None) or getattr(exc, "default_code", "error")
    error = _error(code, str(detail) or "Request failed.", response.status_code)
    for header in ("Allow", "Retry-After", "WWW-Authenticate"):
        if header in response:
            error[header] = response[header]
    return error
