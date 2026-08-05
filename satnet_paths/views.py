"""Satnet Path screens. §9, sections 26.9 to 26.13."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView

from accounts.mixins import AuditedPermissionRequiredMixin
from satnet_paths import lifecycle, selectors, services
from satnet_paths.constants import (
    MANAGE_SATNET_PATHS,
    PLAN_SATNET_PATH,
    REVISE_SATNET_PATH,
    VIEW_SATNET_PATH,
)
from satnet_paths.forms import SatnetPathForm
from satnet_paths.models import SatnetPath
from satnets import selectors as satnet_selectors
from satnets.models import Satnet


class SatnetPathDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    permission_required = VIEW_SATNET_PATH
    template_name = "satnet_paths/detail.html"
    context_object_name = "path"

    def get_queryset(self) -> Any:
        return selectors.visible(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        path = self.object
        context["transitions"] = lifecycle.offered_to(self.request.user, path)
        context["chain"] = lifecycle.revision_chain(path)
        context["decisions"] = path.approval_decisions.select_related("decided_by")
        context["editable"] = path.status in lifecycle.EDITABLE_STATUSES
        return context


class _PathScoped(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """A view acting on one existing Path, with the object resolved before the check."""

    @property
    def path(self) -> SatnetPath:
        if not hasattr(self, "_path"):
            self._path = get_object_or_404(
                selectors.visible(self.request.user), pk=self.kwargs["pk"]
            )
        return self._path

    def get_permission_object(self) -> SatnetPath:
        return self.path

    def _refused(self, message: str, status: int = 409) -> HttpResponse:
        return render(
            self.request,
            "satnet_paths/refused.html",
            {"path": self.path, "message": message},
            status=status,
        )


class SatnetPathTransitionView(_PathScoped):
    """One view for every move in the §15.2 graph except the two that need an approver.

    A view per transition would be six near-identical classes whose only difference is a
    string — and whose capability declarations would drift. The action comes from the URL and
    the capability comes from the graph, so a transition added to
    :data:`satnet_paths.lifecycle.TRANSITIONS` is reachable without touching this file.
    """

    def get_permission_required(self) -> Any:
        try:
            return (lifecycle.find(self.path.status, self.kwargs["action"]).capability,)
        except lifecycle.IllegalTransition:
            # An illegal move is not a permission question, but the mixin has to answer one
            # before the POST body runs. Requiring the general capability keeps an
            # unauthenticated or unprivileged caller out, and `post` then reports the real
            # reason with the right status code.
            return (MANAGE_SATNET_PATHS,)

    def post(self, request: HttpRequest, pk: Any, action: str) -> HttpResponse:
        try:
            lifecycle.transition(
                actor=request.user,
                path=self.path,
                action=action,
                reason=request.POST.get("change_reason", ""),
                expected_version=_submitted_version(request),
            )
        except lifecycle.IllegalTransition as exc:
            return self._refused(str(exc), status=409)
        except lifecycle.StaleRecordError as exc:
            return self._stale(exc)
        except lifecycle.TransitionRefused as exc:
            return self._refused(str(exc))

        messages.success(request, f"{self.path.code} is now {self.path.status}.")
        return redirect(self.path.get_absolute_url())

    def _stale(self, exc: lifecycle.StaleRecordError) -> HttpResponse:
        return render(
            self.request,
            "satnet_paths/stale.html",
            {"path": self.path, "changes": exc.changes, "current_version": exc.current_version},
            status=409,
        )


class SatnetPathEditView(_PathScoped):
    """Field edits, which §15.4 allows only in `DRAFT` and `PLANNED`."""

    permission_required = PLAN_SATNET_PATH

    def get(self, request: HttpRequest, pk: Any) -> HttpResponse:
        form = SatnetPathForm(instance=self.path)
        return render(request, "satnet_paths/edit.html", self._context(form))

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        form = SatnetPathForm(request.POST, instance=self.path)
        if not form.is_valid():
            return render(request, "satnet_paths/edit.html", self._context(form), status=400)

        try:
            lifecycle.edit(
                actor=request.user,
                path=self.path,
                values=form.cleaned_data,
                expected_version=_submitted_version(request) or 0,
                reason=request.POST.get("change_reason", ""),
            )
        except lifecycle.StaleRecordError as exc:
            # 409 with the differences, not a form error: §15.5 asks the operator to see what
            # moved. Re-reading the record first, so the diff shows what is there *now*.
            self.path.refresh_from_db()
            return render(
                request,
                "satnet_paths/stale.html",
                {
                    "path": self.path,
                    "changes": exc.changes,
                    "current_version": exc.current_version,
                },
                status=409,
            )
        except lifecycle.NotEditable as exc:
            return self._refused(str(exc))
        except services.PathBlockedError as exc:
            return render(
                request,
                "satnet_paths/edit.html",
                {**self._context(form), "findings": exc.findings},
                status=409,
            )

        messages.success(request, f"Updated {self.path.code}.")
        return redirect(self.path.get_absolute_url())

    def _context(self, form: SatnetPathForm) -> dict[str, Any]:
        return {"form": form, "path": self.path, "satnet": self.path.satnet}


class SatnetPathReviseView(_PathScoped):
    """§15.4 — a new revision, never an overwrite."""

    permission_required = REVISE_SATNET_PATH

    def get(self, request: HttpRequest, pk: Any) -> HttpResponse:
        return render(
            request,
            "satnet_paths/revise.html",
            {"form": SatnetPathForm(instance=self.path), "path": self.path},
        )

    def post(self, request: HttpRequest, pk: Any) -> HttpResponse:
        form = SatnetPathForm(request.POST, instance=self.path)
        if not form.is_valid():
            return render(
                request,
                "satnet_paths/revise.html",
                {"form": form, "path": self.path},
                status=400,
            )
        try:
            successor = lifecycle.revise(
                actor=request.user,
                path=self.path,
                values=form.cleaned_data,
                change_effective_at=form.cleaned_data.get("valid_from"),
                reason=request.POST.get("change_reason", ""),
                expected_version=_submitted_version(request),
            )
        except lifecycle.StaleRecordError as exc:
            return render(
                request,
                "satnet_paths/stale.html",
                {"path": self.path, "changes": exc.changes, "current_version": exc.current_version},
                status=409,
            )
        except lifecycle.TransitionRefused as exc:
            return self._refused(str(exc))
        except services.PathBlockedError as exc:
            return render(
                request,
                "satnet_paths/revise.html",
                {"form": form, "path": self.path, "findings": exc.findings},
                status=409,
            )

        messages.success(request, f"{successor.code} is now revision {successor.revision_number}.")
        return redirect(successor.get_absolute_url())


def _submitted_version(request: HttpRequest) -> int | None:
    """The ``record_version`` the operator's page was rendered against. §15.5.

    Absent means "no opinion", which is what a service call from a script has. A page that
    carries one gets the stale check; one that does not, does not — and the forms all carry it.
    """
    raw = request.POST.get("record_version")
    return int(raw) if raw and raw.isdigit() else None


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
