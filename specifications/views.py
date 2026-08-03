"""Specification Dictionary views.

The list screen is the "one central screen" of acceptance criterion 26.2, from which an
administrator manages display names, descriptions, units, help text, visibility and order.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, View

from accounts.mixins import AuditedPermissionRequiredMixin
from specifications import selectors, services
from specifications.constants import CHANGE_SPECIFICATION, VIEW_SPECIFICATION
from specifications.forms import SpecificationForm
from specifications.models import SpecificationDefinition


class SpecificationListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    """The central Specification Dictionary screen.

    Readable by every authenticated role — an Operator needs to look up what a code
    means — but editable only by an administrator.
    """

    permission_required = VIEW_SPECIFICATION
    template_name = "specifications/list.html"
    context_object_name = "definitions"

    def get_queryset(self) -> QuerySet[SpecificationDefinition]:
        return SpecificationDefinition.objects.select_related("category")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["grouped"] = selectors.grouped_by_category()
        context["can_edit"] = self.request.user.has_perm(CHANGE_SPECIFICATION)
        # Surfaced rather than hidden: a specification with no description is an
        # unanswered engineering question, and section 26.20 says those stay visible.
        context["incomplete"] = selectors.incomplete_definitions()
        return context


class SpecificationDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    """One specification in full."""

    permission_required = VIEW_SPECIFICATION
    template_name = "specifications/detail.html"
    context_object_name = "definition"
    slug_field = "code"
    slug_url_kwarg = "code"

    def get_queryset(self) -> QuerySet[SpecificationDefinition]:
        return SpecificationDefinition.objects.select_related("category")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["can_edit"] = self.request.user.has_perm(CHANGE_SPECIFICATION)
        return context


class SpecificationUpdateView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Edit a specification's presentation metadata."""

    permission_required = CHANGE_SPECIFICATION

    def get(self, request: HttpRequest, code: str) -> HttpResponse:
        definition = get_object_or_404(SpecificationDefinition, code=code)
        return self._render(request, definition, SpecificationForm(instance=definition))

    def post(self, request: HttpRequest, code: str) -> HttpResponse:
        definition = get_object_or_404(SpecificationDefinition, code=code)
        form = SpecificationForm(request.POST, instance=definition)

        if not form.is_valid():
            return self._render(request, definition, form, status=400)

        try:
            services.update_specification(
                actor=request.user,
                specification=definition,
                changes=form.changed_values(),
                expected_version=form.cleaned_data["expected_version"],
                reason=form.cleaned_data["reason"],
            )
        except services.StaleRecordError as exc:
            # Specification section 15.5: a stale submission is rejected, and the user is
            # shown current values rather than having their edit silently dropped.
            definition.refresh_from_db()
            form = SpecificationForm(instance=definition)
            messages.warning(request, str(exc))
            return self._render(request, definition, form, status=409)

        messages.success(request, f"Specification {definition.code} updated.")
        return redirect("specifications:detail", code=definition.code)

    def _render(
        self,
        request: HttpRequest,
        definition: SpecificationDefinition,
        form: SpecificationForm,
        status: int = 200,
    ) -> HttpResponse:
        return render(
            request,
            "specifications/edit.html",
            {"definition": definition, "form": form},
            status=status,
        )
