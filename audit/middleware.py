"""Request-context middleware.

Publishes the request ID, source IP and user agent that specification section 18
requires on every audit event, so services never need an ``HttpRequest`` parameter.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from audit import context

# Header a reverse proxy may use to propagate a correlation ID. nginx is configured to
# pass it through; if absent, one is generated so every request is still correlatable.
REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"

RESPONSE_HEADER = "X-Request-ID"


class RequestContextMiddleware:
    """Bind per-request audit context for the lifetime of the request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _incoming_request_id(request) or uuid.uuid4()
        request.request_id = request_id  # type: ignore[attr-defined]

        with context.bind(
            context.RequestContext(
                request_id=request_id,
                source_ip=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:512],
            )
        ):
            response = self.get_response(request)

        # Echoed so an operator reporting a problem can quote an identifier that appears
        # in both the application log and the audit trail.
        response[RESPONSE_HEADER] = str(request_id)
        return response


def _incoming_request_id(request: HttpRequest) -> uuid.UUID | None:
    raw = request.META.get(REQUEST_ID_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        # A malformed header is ignored rather than rejected: a correlation ID is
        # diagnostic metadata, not something a client should be able to fail a request
        # with, and it must never be trusted as input.
        return None


def _client_ip(request: HttpRequest) -> str | None:
    """Return the client address.

    ``REMOTE_ADDR`` is used directly rather than parsing ``X-Forwarded-For``. Behind the
    nginx configuration in docker/nginx, ``X-Real-IP`` is set by a proxy we control, but
    a spoofable header must never become an audit record's source IP. If a deployment
    ever needs the forwarded address, that is a settings-driven trusted-proxy
    configuration, not a default.
    """
    return request.META.get("REMOTE_ADDR") or None
