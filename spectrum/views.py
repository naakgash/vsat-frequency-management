"""The read-only spectrum view over a Beam direction. §13.11, §16.

Read-only is not a phase. §13.11 says there is no write route to the reservation table for
any role, so this module has no form, no POST handler and no service import that writes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from accounts.mixins import AuditedPermissionRequiredMixin
from beams import selectors as beam_selectors
from beams.constants import Direction
from beams.models import Beam, BeamDirectionConfig
from calculations.gaps import CapacitySummary
from spectrum import selectors
from spectrum.models import SpectrumReservation


class BeamSpectrumView(LoginRequiredMixin, AuditedPermissionRequiredMixin, TemplateView):
    """Free and occupied spectrum for one Beam, per direction and per leg.

    Scope-filtered through the same queryset every other Beam screen uses, so an out-of-scope
    Beam is a 404 rather than a 403 — the platform does not confirm the existence of objects
    an actor may not see (**A-17**).
    """

    template_name = "spectrum/beam_spectrum.html"
    permission_required = "beams.view_beam"

    def get_permission_object(self) -> Beam:
        return self.beam

    @property
    def beam(self) -> Beam:
        if not hasattr(self, "_beam"):
            self._beam = get_object_or_404(
                beam_selectors.visible(self.request.user), pk=self.kwargs["pk"]
            )
        return self._beam

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        beam = self.beam
        context["beam"] = beam

        panels = []
        for config in beam_selectors.direction_configs(beam):
            if not config.is_enabled or not config.is_configured:
                continue
            for window in (config.uplink_window, config.downlink_window):
                if window is None:
                    continue
                summary = selectors.capacity(config, leg=window.side)
                panels.append(
                    {
                        "direction": config.direction,
                        "leg": window.side,
                        "window": window,
                        "summary": summary,
                        "reservations": _reservations_for(config, window.side),
                        # Rendered as CSS percentages against the entitlement, so a Beam
                        # narrowed to a sub-range fills its own strip rather than a sliver of
                        # the window's (ADR-0019).
                        "bars": _bars(config, window.side, summary),
                    }
                )
        context["panels"] = panels
        context["directions"] = Direction
        return context


def _reservations_for(config: BeamDirectionConfig, leg: str) -> list[SpectrumReservation]:
    resource_ids = [
        str(link.spectrum_resource_id)
        for link in config.spectrum_resources.all()
        if link.spectrum_resource.leg == leg
    ]
    if not resource_ids:
        return []
    return list(selectors.reservations_on(resource_ids))


def _bars(config: BeamDirectionConfig, leg: str, summary: CapacitySummary) -> list[dict[str, Any]]:
    """Positioned rectangles for the occupancy strip.

    A CSS strip rather than a charting library. §19.4 forbids a CDN, so every chart library is
    a committed binary this repository owns forever, and a spectrum strip is a set of
    rectangles on one linear axis — which CSS does natively, keyboard-accessibly and with the
    numbers still present as text for a screen reader. S13's dashboard may still want real
    charting; this screen does not.
    """
    if summary.total_hz == 0:
        return []
    entitlements = selectors.active_assignments(config, window_id=_window_id(config, leg))
    if not entitlements:
        return []

    low = min(a.rf_start_hz for a in entitlements)
    high = max(a.rf_end_hz for a in entitlements)
    span = high - low
    if span <= 0:
        return []

    bars = []
    for row in _reservations_for(config, leg):
        start = max(row.allocated_start_hz, low)
        end = min(row.allocated_end_hz, high)
        if end <= start:
            continue
        bars.append(
            {
                "left": 100 * (start - low) / span,
                "width": 100 * (end - start) / span,
                "reservation": row,
            }
        )
    return bars


def _window_id(config: BeamDirectionConfig, leg: str) -> UUID | None:
    for window in (config.uplink_window, config.downlink_window):
        if window is not None and window.side == leg:
            return window.pk
    return None
