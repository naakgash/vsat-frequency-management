"""The Engineering Preview.

A sandbox over the engine. It reads nothing and writes nothing: no model, no audit record,
no session state. That is what makes it safe to hand to any authenticated user, and it is
also what makes it useful this early — the arithmetic can be exercised and argued about
before a single Beam or Satnet Path exists.

Specification section 11 says the backend result is authoritative and that no template or
script recalculates it. This screen is the demonstration of that rule: everything on it is
computed here and rendered as text.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.generic import View

from calculations import bandwidth, guards, validation
from calculations.forms import PreviewForm
from calculations.ranges import FrequencyRange
from calculations.types import Placement


class PreviewView(LoginRequiredMixin, View):
    """Calculate a placement and show every intermediate value.

    Read-only, so there is no capability beyond being signed in. It exposes no data —
    every number on the page came from the form that was just submitted — so there is
    nothing to scope and nothing to audit.
    """

    template_name = "calculations/preview.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, {"form": PreviewForm()})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = PreviewForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)

        placement = self._calculate(form)
        window = self._window(form)
        findings = validation.check_placement(
            placement,
            window=window,
            min_edge_guard_hz=form.cleaned_data.get("min_edge_guard_hz") or 0,
        )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "placement": placement,
                "window": window,
                "findings": findings,
                "blocking": validation.blocking(findings),
                "is_placeable": validation.is_placeable(findings),
                "steps": self._steps(placement),
            },
        )

    @staticmethod
    def _calculate(form: PreviewForm) -> Placement:
        request = form.bandwidth_request()
        _, occupied_bw = bandwidth.resolve_request(request)
        # The guard depends on the occupied bandwidth, and the allocated range depends on
        # the guard, so the order is fixed: derive, resolve, place.
        widths = guards.resolve(form.guard_policy(), occupied_bw)
        return bandwidth.place(
            request=request,
            centre_hz=form.cleaned_data["centre_hz"],
            guards=widths,
        )

    @staticmethod
    def _window(form: PreviewForm) -> FrequencyRange | None:
        bounds = form.window_bounds()
        return FrequencyRange(*bounds) if bounds else None

    @staticmethod
    def _steps(placement: Placement) -> list[dict[str, Any]]:
        """The calculation, one row per step, in the order it was performed.

        Section 9.4 requires derived values to be shown with their formula rather than as
        bare numbers. An operator who can see how a figure was reached can tell a wrong
        input from a wrong tool; one who cannot has to trust it.
        """
        half = placement.occupied.width_hz // 2
        return [
            {
                "label": "Occupied bandwidth",
                "formula": "symbol rate x (1 + roll-off), rounded up",
                "value_hz": placement.occupied_bandwidth_hz,
                "detail": (f"{placement.symbol_rate_sps:,} x (1 + {placement.rolloff})"),
            },
            {
                "label": "Half width",
                "formula": "occupied bandwidth / 2, rounded up",
                "value_hz": half,
                "detail": "Rounded outward, so an odd bandwidth widens rather than narrows.",
            },
            {
                "label": "Occupied range",
                "formula": "centre -/+ half width",
                "value_hz": placement.occupied.width_hz,
                "detail": f"{placement.occupied} Hz, half-open",
            },
            {
                "label": "Guards",
                "formula": placement.guards.policy_label or "no policy applied",
                "value_hz": placement.guards.total_hz,
                "detail": (
                    f"{placement.guards.left_hz:,} Hz left, "
                    f"{placement.guards.right_hz:,} Hz right "
                    f"(source: {placement.guards.source})"
                ),
            },
            {
                "label": "Allocated range",
                "formula": "occupied range widened by both guards",
                "value_hz": placement.allocated_bandwidth_hz,
                "detail": f"{placement.allocated} Hz — this is what is checked for overlap",
            },
        ]
