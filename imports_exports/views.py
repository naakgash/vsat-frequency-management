"""Download endpoints. §17.2.

A GET, deliberately. An export changes nothing, and making it a POST would break the one thing
people actually do with these links: paste them to each other. The filters ride in the query
string exactly as they do on the table, so the URL that produced a file is the URL that
reproduces it.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.views import View

from accounts.mixins import AuditedPermissionRequiredMixin
from imports_exports import services
from imports_exports.constants import EXPORT_DATA
from reporting import filters as filter_registry


class SatnetPathExportView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """The normalized export of whatever the table is currently showing."""

    permission_required = EXPORT_DATA

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        export = services.export_satnet_paths(
            actor=request.user,
            filters=filter_registry.clean(request.GET.dict()),
            columns=request.GET.getlist("column"),
            sort=request.GET.get("sort", ""),
            reason=request.GET.get("reason", ""),
        )
        response = HttpResponse(export.content, content_type=export.content_type)
        response["Content-Disposition"] = f'attachment; filename="{export.filename}"'
        # So a caller can tell an empty result from a failed one without opening the file.
        response["X-Export-Rows"] = str(export.row_count)
        return response
