"""The Satnet Path table, its saved views, and the dashboard. §10.3, §26.11."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import TemplateView

from accounts.mixins import AuditedPermissionRequiredMixin
from reporting import columns as column_registry
from reporting import filters as filter_registry
from reporting import selectors, services
from reporting.constants import MANAGE_SAVED_VIEWS
from reporting.models import SavedView
from satnet_paths.constants import VIEW_SATNET_PATH


class DashboardView(LoginRequiredMixin, TemplateView):
    """The front page. Authenticated, scope-filtered, and computed on every load (§16)."""

    template_name = "reporting/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {
            **super().get_context_data(**kwargs),
            "dashboard": selectors.dashboard(self.request.user),
        }


class SatnetPathTableView(LoginRequiredMixin, AuditedPermissionRequiredMixin, TemplateView):
    """§10.3's table: grouped columns, filters, sorting and saved views.

    **The URL is the state.** Filters, chosen columns and the sort all live in the query
    string, so applying a saved view is a redirect and a shared link reproduces exactly what
    the sender was looking at. Holding any of it in the session would give two people the same
    URL and different tables.
    """

    permission_required = VIEW_SATNET_PATH
    template_name = "reporting/satnet_path_table.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        request = self.request
        active_filters = filter_registry.clean(request.GET.dict())
        chosen = request.GET.getlist("column")
        sort = request.GET.get("sort", "")

        chosen_columns = column_registry.resolve(chosen)
        return {
            **super().get_context_data(**kwargs),
            "paths": selectors.table(request.user, filters=active_filters, sort=sort),
            "columns": chosen_columns,
            "chosen_keys": [column.key for column in chosen_columns],
            "groups": column_registry.grouped(),
            "filters": filter_registry.FILTERS,
            "active_filters": active_filters,
            "applied": filter_registry.describe(active_filters),
            "sort": sort,
            "saved_views": selectors.views_for(request.user),
        }


class SaveViewView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Save the current table setup under a name. §10.3."""

    permission_required = MANAGE_SAVED_VIEWS

    def post(self, request: HttpRequest) -> HttpResponse:
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "A saved view needs a name.")
            return redirect(_back_to_table(request))

        try:
            services.save(
                actor=request.user,
                name=name,
                filters=filter_registry.clean(request.POST.dict()),
                columns=request.POST.getlist("column"),
                sort=request.POST.get("sort", ""),
                is_shared=request.POST.get("is_shared") == "on",
            )
        except services.NotYours as exc:
            messages.error(request, str(exc))
            return redirect(_back_to_table(request))

        messages.success(request, f"Saved the view {name}.")
        return redirect(_back_to_table(request))


class DeleteViewView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    permission_required = MANAGE_SAVED_VIEWS

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        view = get_object_or_404(SavedView, pk=pk)
        try:
            services.delete(actor=request.user, view=view)
        except services.NotYours as exc:
            # 403 rather than a message: pointing at somebody else's row is not a mistake the
            # interface can make, so it arrives here only from a hand-made request.
            return render(request, "403.html", {"exception": str(exc)}, status=403)
        messages.success(request, "Deleted the saved view.")
        return redirect("reporting:satnet-paths")


def _back_to_table(request: HttpRequest) -> str:
    """Return to the table with its state intact.

    The query string is rebuilt from the submitted form rather than taken from the referer:
    a referer is optional, forgeable and often stripped, and the form already carries
    everything the table needs to look the same on the way back.
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    parameters: list[tuple[str, str]] = sorted(filter_registry.clean(request.POST.dict()).items())
    parameters += [("column", key) for key in request.POST.getlist("column")]
    if request.POST.get("sort"):
        parameters.append(("sort", request.POST["sort"]))
    query = urlencode(parameters)
    return (
        f"{reverse('reporting:satnet-paths')}?{query}"
        if query
        else reverse("reporting:satnet-paths")
    )
