"""Download and upload endpoints. §17.1, §17.2.

The export is a **GET**, deliberately. It changes nothing, and making it a POST would break the
one thing people actually do with these links: paste them to each other. The filters ride in the
query string exactly as they do on the table, so the URL that produced a file is the URL that
reproduces it.

The import is **POST only**, equally deliberately, and administrator only. A dry run writes a
batch and its rows; a commit writes allocations. Neither is a thing a link should be able to do
to somebody who clicked it.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, TemplateView

from accounts.mixins import AuditedPermissionRequiredMixin
from imports_exports import selectors, services
from imports_exports.constants import COMMIT_IMPORT, EXPORT_DATA, RUN_IMPORT_DRYRUN, BatchPolicy
from imports_exports.importer import fields as field_registry
from imports_exports.importer import mapping, parse
from imports_exports.models import ImportBatch
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


class ImportListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, TemplateView):
    """Every batch, and the form that starts a new one. §17.1."""

    permission_required = RUN_IMPORT_DRYRUN
    template_name = "imports_exports/list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {
            **super().get_context_data(**kwargs),
            "batches": selectors.batches(),
            "policies": BatchPolicy.choices,
            "expected_columns": sorted(field_registry.headings()),
            "not_imported": field_registry.NOT_IMPORTED,
            "max_bytes": parse.MAX_UPLOAD_BYTES,
        }


class DryRunView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Stage one: read the file and say what it would do. §17.1."""

    permission_required = RUN_IMPORT_DRYRUN

    def post(self, request: HttpRequest) -> HttpResponse:
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose a workbook to read.")
            return redirect("imports:list")

        try:
            batch = services.dry_run(
                actor=request.user,
                content=upload.read(),
                file_name=upload.name or "uploaded.xlsx",
                batch_policy=_policy_from(request.POST.get("batch_policy", "")),
                reason=request.POST.get("reason", ""),
            )
        except parse.UnreadableFile as exc:
            messages.error(request, str(exc))
            return redirect("imports:list")

        messages.success(request, f"Read {batch.file_name}: {batch.message}.")
        return redirect(batch.get_absolute_url())


class ImportDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    """The review screen: every row, its classification and why. §17.1."""

    permission_required = RUN_IMPORT_DRYRUN
    template_name = "imports_exports/review.html"
    model = ImportBatch
    context_object_name = "batch"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        batch: ImportBatch = self.object
        return {
            **super().get_context_data(**kwargs),
            "rows": selectors.rows_of(batch, classification=self.request.GET.get("only", "")),
            "summary": selectors.summary(batch),
            "only": self.request.GET.get("only", ""),
            "unresolved": selectors.unresolved_labels(batch),
            "satnets": mapping.candidates(field_registry.SATNET),
            "gateways": mapping.candidates(field_registry.GATEWAY),
        }


class CommitView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Stage two: write the reviewed rows, once the file proves it is the same one. §17.1."""

    permission_required = COMMIT_IMPORT

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        batch = get_object_or_404(ImportBatch, pk=pk)
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(
                request,
                "Attach the file again. A commit verifies that the spreadsheet has not changed "
                "since it was reviewed, and it cannot do that without the file.",
            )
            return redirect(batch.get_absolute_url())

        try:
            services.commit_batch(
                actor=request.user,
                batch=batch,
                content=upload.read(),
                reason=request.POST.get("reason", ""),
            )
        except services.CommitRefused as exc:
            messages.error(request, str(exc))
            return redirect(batch.get_absolute_url())

        messages.success(request, f"Committed {batch.file_name}: {batch.message}.")
        return redirect(batch.get_absolute_url())


class RememberMappingView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Say what a label means, so no import asks again. §17.1."""

    permission_required = RUN_IMPORT_DRYRUN

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        batch = get_object_or_404(ImportBatch, pk=pk)
        try:
            mapping.remember(
                actor=request.user,
                kind=request.POST.get("kind", ""),
                label=request.POST.get("label", ""),
                target_id=uuid.UUID(request.POST["target_id"]),
            )
        except (ValueError, KeyError) as exc:
            return render(request, "403.html", {"exception": str(exc)}, status=400)

        messages.success(
            request,
            "Recorded the mapping. Run the dry run again to see the rows it settles — the "
            "classification is a record of what this file said at the time it was read.",
        )
        return redirect(batch.get_absolute_url())


def _policy_from(value: str) -> str:
    """A batch policy from a form field, defaulting to the cautious one.

    Anything unrecognised becomes all-or-nothing rather than raising: the value arrives from a
    form, and the safe default is the one where a surprise stops the batch.
    """
    return value if value in BatchPolicy.values else BatchPolicy.ALL_OR_NOTHING
