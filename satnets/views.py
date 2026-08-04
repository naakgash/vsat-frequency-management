"""Satnet screens. §6, §25, §26.8."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from accounts.mixins import AuditedPermissionRequiredMixin
from satnets import scope, selectors, services
from satnets.constants import MANAGE_SATNETS, VIEW_SATNET
from satnets.forms import SatnetCreateForm, SatnetEditForm
from satnets.models import Satnet


class SatnetListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    permission_required = VIEW_SATNET
    template_name = "satnets/satnet_list.html"
    context_object_name = "satnets"

    def get_queryset(self) -> Any:
        return selectors.visible(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        # Which of the listed Satnets this actor may actually act on. Shown as a badge rather
        # than by hiding the rest: an operator needs to be able to see what exists in order to
        # ask for access to it, and a filtered list looks like missing data.
        context["actionable_ids"] = set(
            scope.granted_to(self.request.user, selectors.visible(self.request.user)).values_list(
                "id", flat=True
            )
        )
        return context


class SatnetDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    permission_required = VIEW_SATNET
    template_name = "satnets/satnet_detail.html"
    context_object_name = "satnet"

    def get_queryset(self) -> Any:
        return selectors.visible(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        satnet = self.object
        context["capacity"] = selectors.capacity(satnet)
        allowed, reason = scope.may_act_on(
            self.request.user, beam_id=satnet.beam_id, hub_id=satnet.hub_id
        )
        context["may_act"] = allowed
        context["scope_reason"] = reason
        return context


class SatnetCreateView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    permission_required = MANAGE_SATNETS

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = SatnetCreateForm(actor=request.user)
        return render(request, "satnets/satnet_form.html", {"form": form, "creating": True})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = SatnetCreateForm(request.POST, actor=request.user)
        if form.is_valid():
            try:
                satnet = services.create(
                    actor=request.user,
                    values=form.cleaned_data,
                    reason=request.POST.get("change_reason", ""),
                )
            except services.OutOfScopeError as exc:
                # 403 rather than a form error: this is a permissions refusal, not a
                # correction the operator can make by retyping. The message names the missing
                # grant so they know who to ask.
                return render(
                    request,
                    "satnets/scope_denied.html",
                    {"reason": str(exc)},
                    status=403,
                )
            messages.success(request, f"Created Satnet {satnet.code}.")
            return redirect(satnet.get_absolute_url())
        return render(
            request, "satnets/satnet_form.html", {"form": form, "creating": True}, status=400
        )


class SatnetEditView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    permission_required = MANAGE_SATNETS

    def get_permission_object(self) -> Satnet:
        return get_object_or_404(Satnet, pk=self.kwargs["pk"])

    def get(self, request: HttpRequest, pk: Any) -> HttpResponse:
        satnet = get_object_or_404(selectors.visible(request.user), pk=pk)
        form = SatnetEditForm(instance=satnet, actor=request.user)
        return render(
            request, "satnets/satnet_form.html", {"form": form, "satnet": satnet, "creating": False}
        )

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        satnet = get_object_or_404(selectors.visible(request.user), pk=pk)
        form = SatnetEditForm(request.POST, instance=satnet, actor=request.user)
        if form.is_valid():
            try:
                services.update(
                    actor=request.user,
                    # The stored row, not the form's instance: ModelForm._post_clean has
                    # already written the submitted values onto that object, so the audit
                    # `before` snapshot would otherwise record the new values as the old ones.
                    # The same trap as inventory's edit view in S5.
                    satnet=Satnet.objects.get(pk=satnet.pk),
                    values=form.cleaned_data,
                    reason=request.POST.get("change_reason", ""),
                )
            except services.OutOfScopeError as exc:
                return render(
                    request, "satnets/scope_denied.html", {"reason": str(exc)}, status=403
                )
            messages.success(request, f"Updated Satnet {satnet.code}.")
            return redirect(satnet.get_absolute_url())
        return render(
            request,
            "satnets/satnet_form.html",
            {"form": form, "satnet": satnet, "creating": False},
            status=400,
        )


class SatnetActivationView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    permission_required = MANAGE_SATNETS

    def get_permission_object(self) -> Satnet:
        return get_object_or_404(Satnet, pk=self.kwargs["pk"])

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        satnet = get_object_or_404(selectors.visible(request.user), pk=pk)
        active = request.POST.get("active") == "true"
        try:
            services.set_active(
                actor=request.user,
                satnet=satnet,
                active=active,
                reason=request.POST.get("change_reason", ""),
            )
        except services.OutOfScopeError as exc:
            return render(request, "satnets/scope_denied.html", {"reason": str(exc)}, status=403)
        messages.success(
            request, f"{'Reactivated' if active else 'Deactivated'} Satnet {satnet.code}."
        )
        return redirect(satnet.get_absolute_url())
