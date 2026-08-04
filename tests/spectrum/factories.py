"""Builders for reservation tests.

Every frequency here exists only to satisfy a constraint. They are **not** RF engineering
values, and nothing is seeded from them.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from django.utils import timezone

from beams.constants import Direction
from beams.models import BeamDirectionSpectrumResource, BeamSpectrumAssignment
from inventory.constants import SpectrumLeg, TranslationMethod
from inventory.models import SpectrumResource
from spectrum.constants import ReservationKind, ReservationStatus
from spectrum.models import SpectrumReservation
from tests.beams.factories import configure_direction, make_beam
from tests.inventory.factories import make_frequency_window, make_payload_path

#: Where the fixture's downlink band starts. An arbitrary offset with one property that
#: matters: it is far enough from the uplink that a test reading two frequencies can tell at a
#: glance which side it is looking at.
DOWNLINK_BASE_HZ = 19_000_000_000


@dataclasses.dataclass
class Entitlement:
    """A Beam direction with one assignment and one resource — the smallest thing that can
    hold a reservation."""

    beam: Any
    config: Any
    assignment: BeamSpectrumAssignment
    resource: SpectrumResource

    @property
    def satellite(self):
        return self.beam.satellite


def make_entitlement(
    code: str = "R",
    *,
    satellite=None,
    resource: SpectrumResource | None = None,
    start_hz: int = 0,
    end_hz: int = 1_000_000_000,
) -> Entitlement:
    """One Beam, one FWD direction, one wide assignment, one hub-uplink resource.

    The assignment is deliberately far wider than any test needs, so a test about *overlap*
    fails on the exclusion constraint rather than on containment. `test_an_allocation_cannot
    _escape_its_assignment` narrows it explicitly.

    Passing ``resource`` makes two entitlements share one — which is the case the OQ-25 answer
    is about, and the case the superseded Beam-keyed constraint got wrong.
    """
    beam = make_beam(f"BEAM-{code}", satellite=satellite)
    # Built at the requested width rather than widened afterwards: `fk_assignment_window_edges`
    # refuses to let a window's edges move while an assignment references them, which is the
    # constraint working. Tests that want a narrower entitlement ask for one here.
    #
    # Both windows are built to match, and the translation maps one onto the other. A widened
    # uplink under the factory's default 10 GHz offset would
    # translate to a downlink outside its own window — which is a *correct* refusal, and a
    # confusing fixture: every test would fail on containment before reaching what it meant to
    # check.
    downlink_start = DOWNLINK_BASE_HZ + start_hz
    path = make_payload_path(
        satellite=beam.satellite,
        code=f"PP-{code}",
        direction=Direction.FWD,
        uplink_window=make_frequency_window(
            beam.satellite,
            f"FW-{code}-UL",
            SpectrumLeg.HUB_UPLINK,
            band=beam.band,
            rf_start_hz=start_hz,
            rf_end_hz=end_hz,
        ),
        downlink_window=make_frequency_window(
            beam.satellite,
            f"FW-{code}-DL",
            SpectrumLeg.REMOTE_DOWNLINK,
            band=beam.band,
            rf_start_hz=downlink_start,
            rf_end_hz=DOWNLINK_BASE_HZ + end_hz,
        ),
        translation_method=TranslationMethod.OFFSET_ADD,
        translation_constant_hz=DOWNLINK_BASE_HZ,
    )
    config = configure_direction(beam, Direction.FWD, payload_path=path)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)

    assignment = config.spectrum_assignments.get(frequency_window=config.uplink_window)

    # Master data is commissioned before it is used, and the fixtures say so. Without this the
    # Beam, its windows and its assignments all begin at the instant the test created them, and
    # an allocation starting "now" — truncated to the minute by a datetime-local field — starts
    # *before* its parents and is correctly refused by the OQ-32 containment rule. A real Beam
    # is not commissioned in the same minute its first allocation is planned.
    commissioned = timezone.now() - timezone.timedelta(days=7)
    beam.effective_from = commissioned
    beam.save(update_fields=["effective_from"])
    config.spectrum_assignments.update(effective_from=commissioned)
    assignment.refresh_from_db()

    if resource is None:
        resource = SpectrumResource.objects.get(
            pk=config.spectrum_resources.first().spectrum_resource_id
        )
    else:
        # Repoint the direction's hub-uplink mapping at the shared resource, and drop the one
        # the factory made for it. Setting only ``Entitlement.resource`` would make
        # ``reserve_range`` write to the shared resource while ``selectors.capacity`` still
        # read the private one — the two Beams would look like they compete and measure as if
        # they did not, which is precisely the bug this fixture exists to catch.
        config.spectrum_resources.filter(spectrum_resource__leg=resource.leg).delete()
        BeamDirectionSpectrumResource.objects.create(
            direction_config=config, spectrum_resource=resource
        )
    return Entitlement(beam=beam, config=config, assignment=assignment, resource=resource)


def reserve_range(
    setup: Entitlement,
    occupied_start_hz: int,
    occupied_end_hz: int,
    *,
    guard_hz: int = 0,
    occupied_overhang_hz: int = 0,
    leg: str = "HUB_UPLINK",
    status: str = ReservationStatus.ON_AIR,
    kind: str = ReservationKind.SATNET_PATH,
    direction: str = Direction.FWD,
    satnet_path_id: uuid.UUID | None = -1,  # type: ignore[assignment]
    reserves: bool = True,
    valid_from=None,
    valid_until=None,
) -> SpectrumReservation:
    """Write one occupancy row directly, bypassing the service.

    Direct writes are the point: these tests exist to prove the *database* refuses, so they
    must be able to construct rows a careful service would never produce.

    ``occupied_overhang_hz`` pushes the occupied range outside the allocated one, which is the
    only way to exercise `ck_res_occ_in_alloc`.
    """
    return SpectrumReservation.objects.create(
        spectrum_resource=setup.resource,
        beam_spectrum_assignment=setup.assignment,
        assignment_start_hz=setup.assignment.rf_start_hz,
        assignment_end_hz=setup.assignment.rf_end_hz,
        leg=leg,
        polarization="RHCP",
        occupied_start_hz=occupied_start_hz - occupied_overhang_hz,
        occupied_end_hz=occupied_end_hz,
        allocated_start_hz=occupied_start_hz - guard_hz,
        allocated_end_hz=occupied_end_hz + guard_hz,
        valid_from=valid_from or timezone.now(),
        valid_until=valid_until,
        kind=kind,
        satnet_path_id=uuid.uuid4() if satnet_path_id == -1 else satnet_path_id,
        direction=direction,
        status=status,
        reserves_spectrum=reserves,
    )
