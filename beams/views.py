"""Beam screens: the list, the detail page, and the five-step builder.

Beam engineering is administrator-only (§25). Reading is open to any authenticated user,
because an Operator has to pick a Beam when creating a Satnet Path — and every write view
here is reached only through ``beams.services``, which authorises before it transacts.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, View

from accounts.mixins import AuditedPermissionRequiredMixin
from beams import selectors, services, validation
from beams.constants import MANAGE_BEAMS, VIEW_BEAMS, Direction
from beams.forms import WIZARD_STEPS, BeamIdentityForm, DirectionForm
from beams.models import Beam, BeamDirectionConfig


class BeamListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    permission_required = VIEW_BEAMS
    template_name = "beams/beam_list.html"
    context_object_name = "objects"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[Beam]:
        return selectors.for_listing(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["can_manage"] = self.request.user.has_perm(MANAGE_BEAMS)
        return context


class BeamDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    """Specification section 10.1: identity, both chains, and the validation state."""

    permission_required = VIEW_BEAMS
    template_name = "beams/beam_detail.html"
    context_object_name = "beam"

    def get_queryset(self) -> QuerySet[Beam]:
        return selectors.visible(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        beam = context["beam"]
        context["configs"] = selectors.direction_configs(beam)
        context["latest_validation"] = selectors.latest_validation(beam)
        # Validated live rather than read from the cached column: the master data underneath
        # a Beam can be superseded, so a badge from last week is a claim about last week.
        context["report"] = validation.validate(beam)
        context["can_manage"] = self.request.user.has_perm(MANAGE_BEAMS)
        context["steps"] = WIZARD_STEPS
        return context


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------
class BeamCreateView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Step 1 — identity. Creates the Beam and both direction rows."""

    permission_required = MANAGE_BEAMS
    template_name = "beams/builder/step_1.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, self._context(BeamIdentityForm()))

    def post(self, request: HttpRequest) -> HttpResponse:
        form = BeamIdentityForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._context(form), status=400)

        beam = services.create(
            actor=request.user,
            values=form.model_values(),
            reason=form.cleaned_data.get("reason", ""),
        )
        messages.success(request, f"Beam {beam.code} created. Configure its chains next.")
        return redirect("beams:builder-direction", pk=beam.pk, direction=Direction.FWD)

    def _context(self, form: BeamIdentityForm) -> dict[str, Any]:
        return {"form": form, "steps": WIZARD_STEPS, "current_step": 1}


class BeamDirectionView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Steps 2 and 3 — one direction chain each.

    One view for both, because §5.2 and §5.3 describe structurally identical chains. Two
    near-copies would drift the first time a rule changed on one of them.
    """

    permission_required = MANAGE_BEAMS
    template_name = "beams/builder/step_2.html"

    def get(self, request: HttpRequest, pk: str, direction: str) -> HttpResponse:
        beam, config = self._resolve(request, pk, direction)
        return render(
            request, self.template_name, self._context(beam, config, DirectionForm(instance=config))
        )

    def post(self, request: HttpRequest, pk: str, direction: str) -> HttpResponse:
        beam, config = self._resolve(request, pk, direction)
        form = DirectionForm(request.POST, instance=config)
        if not form.is_valid():
            return render(
                request, self.template_name, self._context(beam, config, form), status=400
            )

        # The form binds to `config` and mutates it, so the service receives a fresh copy —
        # the same aliasing that broke the audit "before" snapshot in S5.
        services.update_direction(
            actor=request.user,
            config=BeamDirectionConfig.objects.get(pk=config.pk),
            values=form.model_values(),
            equipment=form.equipment_choices(),
            reason=form.cleaned_data.get("reason", ""),
        )

        if direction == Direction.FWD:
            messages.success(request, "Forward chain saved. Now the return chain.")
            return redirect("beams:builder-direction", pk=beam.pk, direction=Direction.RTN)
        messages.success(request, "Return chain saved. Review the validation next.")
        return redirect("beams:builder-validate", pk=beam.pk)

    def _resolve(
        self, request: HttpRequest, pk: str, direction: str
    ) -> tuple[Beam, BeamDirectionConfig]:
        if direction not in Direction.values:
            raise Http404(f"Unknown direction: {direction}")
        beam = get_object_or_404(selectors.visible(request.user), pk=pk)
        config = get_object_or_404(beam.direction_configs, direction=direction)
        return beam, config

    def _context(
        self, beam: Beam, config: BeamDirectionConfig, form: DirectionForm
    ) -> dict[str, Any]:
        return {
            "beam": beam,
            "config": config,
            "form": form,
            "steps": WIZARD_STEPS,
            "current_step": 2 if config.direction == Direction.FWD else 3,
        }


class BeamValidateView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Step 4 — every rule, with reasons. Specification sections 10.1 and 26.6."""

    permission_required = VIEW_BEAMS
    template_name = "beams/builder/step_4.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        beam = get_object_or_404(selectors.visible(request.user), pk=pk)
        return render(request, self.template_name, self._context(request, beam))

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        """Run and record a validation. A write, so it goes through the service."""
        beam = get_object_or_404(selectors.visible(request.user), pk=pk)
        result = services.validate_beam(
            actor=request.user, beam=beam, reason=request.POST.get("reason", "")
        )
        messages.info(request, f"Validation {result.outcome.lower().replace('_', ' ')}.")
        return redirect("beams:builder-validate", pk=beam.pk)

    def _context(self, request: HttpRequest, beam: Beam) -> dict[str, Any]:
        report = validation.validate(beam)
        return {
            "beam": beam,
            "configs": selectors.direction_configs(beam),
            "report": report,
            "latest_validation": selectors.latest_validation(beam),
            "can_manage": request.user.has_perm(MANAGE_BEAMS),
            "steps": WIZARD_STEPS,
            "current_step": 4,
        }


class BeamActivationView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Step 5 — activation, refused while any enabled direction is invalid (§26.6)."""

    permission_required = MANAGE_BEAMS
    template_name = "beams/builder/step_5.html"

    def get(self, request: HttpRequest, pk: str) -> HttpResponse:
        beam = get_object_or_404(selectors.visible(request.user), pk=pk)
        return render(request, self.template_name, self._context(beam))

    def post(self, request: HttpRequest, pk: str) -> HttpResponse:
        beam = get_object_or_404(selectors.visible(request.user), pk=pk)
        activate = request.POST.get("action") != "deactivate"

        try:
            services.set_active(
                actor=request.user,
                beam=beam,
                active=activate,
                reason=request.POST.get("reason", ""),
            )
        except services.ActivationBlockedError as exc:
            # Refused with the reasons attached. A refusal that only says "no" leaves an
            # administrator with nothing to fix.
            messages.error(request, str(exc))
            return render(request, self.template_name, self._context(beam, refusal=exc), status=409)

        messages.success(request, f"Beam {beam.code} {'activated' if activate else 'deactivated'}.")
        return redirect(beam.get_absolute_url())

    def _context(self, beam: Beam, refusal: Exception | None = None) -> dict[str, Any]:
        return {
            "beam": beam,
            "configs": selectors.direction_configs(beam),
            "report": validation.validate(beam),
            "refusal": refusal,
            "steps": WIZARD_STEPS,
            "current_step": 5,
        }
