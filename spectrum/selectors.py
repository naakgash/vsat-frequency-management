"""Reading spectrum: what is held, and what is free. §16, ADR-0009.

The free-capacity arithmetic itself is in ``calculations.gaps`` and knows nothing about
Django. This module's whole job is to fetch the right rows and hand them over — which is
where the correctness risk actually sits, because the engine will faithfully compute gaps in
whatever entitlement it is given.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from beams.models import BeamDirectionConfig, BeamSpectrumAssignment
from calculations import gaps
from calculations.ranges import FrequencyRange
from spectrum.models import SpectrumReservation


def active_assignments(
    config: BeamDirectionConfig,
    *,
    at: datetime | None = None,
    window_id: UUID | None = None,
) -> list[BeamSpectrumAssignment]:
    """The entitlements in force for this direction at a moment in time.

    ``at`` defaults to now rather than to "ignore time". A direction whose assignment expired
    last week is entitled to nothing today, and the honest answer to "what is free" is then
    "nothing", not "the whole window".
    """
    moment = at or timezone.now()
    queryset = config.spectrum_assignments.filter(
        is_active=True,
        effective_from__lte=moment,
    ).filter(Q(effective_until__isnull=True) | Q(effective_until__gt=moment))
    if window_id is not None:
        queryset = queryset.filter(frequency_window_id=window_id)
    return list(queryset.select_related("frequency_window").order_by("rf_start_hz"))


def reservations_on(
    resource_ids: list[str],
    *,
    at: datetime | None = None,
    exclude_satnet_path_id: UUID | None = None,
) -> QuerySet[SpectrumReservation]:
    """Everything holding spectrum on these resources at a moment in time.

    ``exclude_satnet_path_id`` exists for the revision case (§15.4): an allocation being
    edited must not be shown as competing with itself, or the operator is told their own
    transmission is in the way.
    """
    moment = at or timezone.now()
    queryset = (
        SpectrumReservation.objects.filter(
            spectrum_resource_id__in=resource_ids,
            reserves_spectrum=True,
            valid_from__lte=moment,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=moment))
        .select_related("spectrum_resource")
    )
    if exclude_satnet_path_id is not None:
        queryset = queryset.exclude(satnet_path_id=exclude_satnet_path_id)
    return queryset.order_by("allocated_start_hz")


def capacity(
    config: BeamDirectionConfig,
    *,
    leg: str,
    at: datetime | None = None,
    exclude_satnet_path_id: UUID | None = None,
) -> gaps.CapacitySummary:
    """Free capacity on one leg of one direction, right now.

    Two things this deliberately does **not** do.

    It does not compute across the Frequency Window: the bounds are the direction's active
    assignments (ADR-0019), because a window may be shared and reporting gaps across it would
    offer an operator spectrum belonging to another Beam.

    It does not filter reservations by leg or by assignment. Competition is judged on the
    **resource** (ADR-0018), so everything holding spectrum on the resources this leg occupies
    counts against it — including allocations belonging to entirely different Beams, which is
    the entire point of the OQ-25 answer.
    """
    resource_ids = [
        str(link.spectrum_resource_id)
        for link in config.spectrum_resources.all()
        if link.spectrum_resource.leg == leg
    ]
    window_id = _window_for_leg(config, leg)
    entitlements = [
        FrequencyRange(assignment.rf_start_hz, assignment.rf_end_hz)
        for assignment in active_assignments(config, at=at, window_id=window_id)
    ]
    if not resource_ids or not entitlements:
        return gaps.summarise(entitlements, [])

    occupied = [
        FrequencyRange(row.allocated_start_hz, row.allocated_end_hz)
        for row in reservations_on(
            resource_ids, at=at, exclude_satnet_path_id=exclude_satnet_path_id
        )
    ]
    return gaps.summarise(entitlements, occupied)


def _window_for_leg(config: BeamDirectionConfig, leg: str) -> UUID | None:
    """Which of the direction's two windows covers this leg.

    Read from the stored window's own side rather than derived from the direction, so a
    direction whose windows disagree with its payload path — which `beams.validation` reports
    rather than prevents — produces no entitlement instead of the wrong one.
    """
    for window in (config.uplink_window, config.downlink_window):
        if window is not None and window.side == leg:
            return window.pk
    return None
