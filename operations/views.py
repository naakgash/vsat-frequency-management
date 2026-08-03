"""Operations views: health endpoints, landing page and safe error pages."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from operations import health


class HomeView(TemplateView):
    """Landing page.

    Currently a signpost for the platform. It becomes the dashboard in slice S13.
    """

    template_name = "home.html"


@never_cache
def health_live(request: HttpRequest) -> JsonResponse:
    """Liveness probe: the process is up. Deliberately does not touch the database."""
    return JsonResponse({"status": "ok"}, status=200)


@never_cache
def health_ready(request: HttpRequest) -> JsonResponse:
    """Readiness probe: the database answers and required extensions are present."""
    results = health.run_readiness_checks()
    ready = all(result.ok for result in results)
    payload: dict[str, Any] = {
        "status": "ok" if ready else "unavailable",
        "checks": {result.name: result.status for result in results},
    }
    # Failure detail is included because it names a check and a missing extension, never
    # a hostname, credential or stack trace.
    failures = {r.name: r.detail for r in results if not r.ok and r.detail}
    if failures:
        payload["detail"] = failures
    return JsonResponse(payload, status=200 if ready else 503)


# ---------------------------------------------------------------------------
# Error handlers — no stack traces, no internal detail (section 21.15)
# ---------------------------------------------------------------------------
def permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "403.html", status=403)


def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "500.html", status=500)
