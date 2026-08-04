"""Satnet Path screens. §9, sections 26.9 to 26.13."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView

from accounts.mixins import AuditedPermissionRequiredMixin
from satnet_paths import selectors, services
from satnet_paths.constants import MANAGE_SATNET_PATHS, VIEW_SATNET_PATH
from satnet_paths.forms import SatnetPathForm
from satnets import selectors as satnet_selectors
from satnets.models import Satnet


class SatnetPathListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    permission_required = VIEW_SATNET_PATH
    template_name = "satnet_paths/list.html"
    context_object_name = "paths"

    def get_queryset(self) -> Any:
        return selectors.current(self.request.user)


class SatnetPathDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    permission_required = VIEW_SATNET_PATH
    template_name = "satnet_paths/detail.html"
    context_object_name = "path"

    def get_queryset(self) -> Any:
        return selectors.visible(self.request.user)


class _SatnetScoped(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    permission_required = MANAGE_SATNET_PATHS

    @property
    def satnet(self) -> Satnet:
        if not hasattr(self, "_satnet"):
            self._satnet = get_object_or_404(
                satnet_selectors.visible(self.request.user), pk=self.kwargs["satnet_pk"]
            )
        return self._satnet

    def get_permission_object(self) -> Satnet:
        return self.satnet


class SatnetPathCreateView(_SatnetScoped):
    """The §9 workflow, on one page rather than five.

    The plan called for HTMX fragments per step. A single form with a live preview is the same
    workflow with one fewer thing to get wrong: the wizard's value is the *preview*, not the
    pagination, and a multi-step flow holding half an allocation in the session is a second
    place for it to be wrong. The preview and the save call the same function, so what is shown
    is what is checked.
    """

    def get(self, request: HttpRequest, satnet_pk: Any) -> HttpResponse:
        return render(
            request,
            "satnet_paths/wizard/create.html",
            {"form": SatnetPathForm(), "satnet": self.satnet},
        )

    def post(self, request: HttpRequest, satnet_pk: Any) -> HttpResponse:
        form = SatnetPathForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "satnet_paths/wizard/create.html",
                {"form": form, "satnet": self.satnet},
                status=400,
            )

        previewing = request.POST.get("action") == "preview"
        if previewing:
            proposal = services.preview(
                satnet=self.satnet,
                direction=form.cleaned_data["direction"],
                input_mode=form.cleaned_data["input_mode"],
                input_value=form.cleaned_data["input_value"],
                rolloff=form.cleaned_data["rolloff"],
                centre_hz=form.cleaned_data["canonical_center_hz"],
                valid_from=form.cleaned_data["valid_from"],
                valid_until=form.cleaned_data.get("valid_until"),
                guard_policy=form.cleaned_data.get("guard_policy"),
            )
            return render(
                request,
                "satnet_paths/wizard/create.html",
                {"form": form, "satnet": self.satnet, "proposal": proposal},
            )

        try:
            path = services.create(
                actor=request.user,
                satnet=self.satnet,
                values={**form.cleaned_data, "status": request.POST.get("status", "DRAFT")},
                reason=request.POST.get("change_reason", ""),
            )
        except services.PathBlockedError as exc:
            # 409, not 400: the submission is well-formed and was refused by a rule about the
            # world, not by a field the operator can retype.
            return render(
                request,
                "satnet_paths/wizard/create.html",
                {"form": form, "satnet": self.satnet, "findings": exc.findings},
                status=409,
            )
        messages.success(request, f"Created Satnet Path {path.code}.")
        return redirect(path.get_absolute_url())


class AutoPlaceView(_SatnetScoped):
    """§9.3 — proposes and never saves.

    A POST because it reads reservations and is not idempotent in the sense that matters: the
    answer changes as other people allocate. It writes nothing.
    """

    def post(self, request: HttpRequest, satnet_pk: Any) -> HttpResponse:
        form = SatnetPathForm(request.POST)
        form.is_valid()
        data = form.cleaned_data
        # Auto-place runs on a partially-filled form on purpose: an operator asks "where can
        # this go" before they have a centre frequency, and the centre is the one field it
        # exists to supply. The period still has to be there — it is what the gaps are computed
        # at — so a missing one is refused rather than defaulted to now.
        valid_from = data.get("valid_from")
        if valid_from is None:
            return render(
                request,
                "satnet_paths/wizard/create.html",
                {"form": form, "satnet": self.satnet, "auto_place_failed": True},
                status=400,
            )
        proposal = services.auto_place(
            satnet=self.satnet,
            direction=data.get("direction") or "FWD",
            input_mode=data.get("input_mode") or "OCCUPIED_BW",
            input_value=data.get("input_value") or 0,
            rolloff=data.get("rolloff") or Decimal("0.2"),
            valid_from=valid_from,
            valid_until=data.get("valid_until"),
            guard_policy=data.get("guard_policy"),
        )
        return render(
            request,
            "satnet_paths/wizard/create.html",
            {
                "form": form,
                "satnet": self.satnet,
                "proposal": proposal,
                "auto_placed": proposal is not None,
                "auto_place_failed": proposal is None,
            },
        )
