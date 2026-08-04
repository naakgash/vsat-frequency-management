"""Creating a Satnet Path. sections 9.2 to 9.5, §15.6, sections 26.9 to 26.13, §26.16.

The slice everything else was built for. Three things these tests are really about:

* **the server re-checks on save** — a preview an operator accepted was computed against
  reservations that have since changed;
* **both legs block** (§8.2) — a translated-side-only collision is the one an operator cannot
  see, because they chose an uplink centre that looks clear;
* **the refusal is a screen, not a sentence** (§9.5) — rule, Beam, Window, proposed range,
  conflicting allocation, overlap amount, validity overlap, and somewhere else to go.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from beams.models import BeamDirectionSpectrumResource
from inventory.constants import SpectrumResourceKind
from inventory.models import SpectrumResource
from satnet_paths import services
from satnet_paths.constants import InputMode, PathStatus
from satnet_paths.models import SatnetPath
from satnets import services as satnet_services
from spectrum.models import SpectrumReservation
from tests.factories import make_admin
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


@pytest.fixture
def world():
    """A Satnet on an active, fully configured Beam with a 100 MHz uplink entitlement."""
    setup = make_entitlement(code="SP", start_hz=0, end_hz=100 * MHZ)
    admin = make_admin()
    from beams import services as beam_services

    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    hub = make_hub(make_gateway("GW-SP"), "HUB-SP")
    satnet = satnet_services.create(
        actor=admin,
        values={
            "code": "SN-SP",
            "name": "Paths",
            "beam": setup.beam,
            "hub": hub,
            "effective_from": timezone.now() - timezone.timedelta(days=1),
        },
    )
    return {"setup": setup, "satnet": satnet, "admin": admin}


def _values(world, *, centre=50 * MHZ, code="SP-1", status=PathStatus.PLANNED, **extra):
    values = {
        "code": code,
        "direction": "FWD",
        "status": status,
        "input_mode": InputMode.OCCUPIED_BW,
        "input_value": 10 * MHZ,
        "rolloff": Decimal("0.2"),
        "canonical_center_hz": centre,
        "valid_from": timezone.now(),
    }
    values.update(extra)
    return values


def _create(world, **extra):
    return services.create(
        actor=world["admin"], satnet=world["satnet"], values=_values(world, **extra)
    )


# ---------------------------------------------------------------------------
# The happy path, and what it writes
# ---------------------------------------------------------------------------
def test_a_valid_allocation_is_saved_with_both_sides_stored(world):
    """`docs/design/02` §4.2: both sides are stored, never derived on read.

    The translated side is a function of the canonical side and the Payload Path — and the
    Payload Path is master-data versioned, so recomputing on read would silently rewrite an
    allocation's history the moment its path was superseded.
    """
    path = _create(world)

    assert path.canonical_occupied_start_hz < path.canonical_center_hz
    assert path.translated_occupied_start_hz != path.canonical_occupied_start_hz
    assert path.translated_allocated_end_hz - path.translated_allocated_start_hz == (
        path.canonical_allocated_end_hz - path.canonical_allocated_start_hz
    ), "translation preserves width exactly (§13.7)"


def test_saving_writes_a_reservation_per_resource_per_leg(world):
    """**A-23**, not "exactly two rows".

    `docs/design/02` §5 said two — one per leg — and the OQ-25 answer superseded that:
    *"an allocation may reserve more than one spectrum resource."* This Beam's uplink leg is on
    two chains, so the uplink alone writes two.
    """
    config = world["setup"].config
    second = SpectrumResource.objects.create(
        satellite=world["satnet"].beam.satellite,
        code="SR-SECOND-CHAIN",
        name="Second chain",
        kind=SpectrumResourceKind.RF_CHAIN,
        leg="HUB_UPLINK",
        effective_from="2026-01-01T00:00:00Z",
    )
    BeamDirectionSpectrumResource.objects.create(direction_config=config, spectrum_resource=second)

    path = _create(world)

    rows = SpectrumReservation.objects.filter(satnet_path_id=path.pk)
    assert rows.count() == 3, "two uplink chains plus one downlink"
    assert rows.filter(leg="HUB_UPLINK").count() == 2


def test_a_draft_reserves_nothing(world):
    """§15.2 and **OQ-32**: a draft *"must not reserve spectrum, be provisioned or become
    operational."*"""
    path = _create(world, status=PathStatus.DRAFT)

    assert SpectrumReservation.objects.filter(satnet_path_id=path.pk).count() == 0


def test_the_reservations_hold_the_allocated_range_including_guards(world):
    """§8.1. Guards are part of what is reserved, or two transmissions whose guards meet would
    be accepted."""
    path = _create(world, guard_policy=None)

    row = SpectrumReservation.objects.filter(satnet_path_id=path.pk, leg="HUB_UPLINK").first()
    assert row.allocated_start_hz == path.canonical_allocated_start_hz
    assert row.allocated_end_hz == path.canonical_allocated_end_hz


def test_both_input_modes_produce_the_same_allocation(world):
    """§9.2 offers both and each derives the other. The stored record keeps the one the
    operator typed — storing both independently would let them drift, which is exactly what
    §9.2 forbids."""
    by_bandwidth = _create(world, code="BW", input_mode=InputMode.OCCUPIED_BW, input_value=12 * MHZ)
    rate = by_bandwidth.symbol_rate_sps

    world["satnet"].refresh_from_db()
    by_rate = services.preview(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.SYMBOL_RATE,
        input_value=rate,
        rolloff=Decimal("0.2"),
        centre_hz=50 * MHZ,
        valid_from=timezone.now(),
    )

    assert by_rate.occupied_bw_hz == by_bandwidth.occupied_bw_hz


# ---------------------------------------------------------------------------
# §26.16 — derived fields are system-owned
# ---------------------------------------------------------------------------
def test_submitted_derived_values_are_ignored(world):
    """§26.16. A caller supplying its own occupied bandwidth, ranges or Beam must not be able
    to overwrite what the engine computed — the whole guarantee rests on the stored values
    coming from one place."""
    other = make_entitlement(code="ELSEWHERE")

    path = services.create(
        actor=world["admin"],
        satnet=world["satnet"],
        values=_values(
            world,
            occupied_bw_hz=999,
            allocated_bw_hz=999,
            canonical_allocated_start_hz=1,
            canonical_allocated_end_hz=2,
            beam=other.beam,
            symbol_rate_sps=1,
        ),
    )

    assert path.occupied_bw_hz != 999
    assert path.canonical_allocated_start_hz != 1
    assert path.beam_id == world["satnet"].beam_id


# ---------------------------------------------------------------------------
# §9.5 — the blocking message
# ---------------------------------------------------------------------------
def test_an_overlapping_allocation_is_refused_with_everything_the_message_needs(world):
    """§9.5's list, item by item. A refusal that says only "this overlaps" makes an operator
    open three other screens to find out what, where, and by how much."""
    existing = reserve_range(world["setup"], 48 * MHZ, 52 * MHZ)

    with pytest.raises(services.PathBlockedError) as excinfo:
        _create(world, centre=50 * MHZ)

    finding = next(f for f in excinfo.value.findings if f.code == "SPECTRUM_CONFLICT")
    assert finding.rule == "section 8.1"
    assert finding.beam_code == world["satnet"].beam.code
    assert finding.window_code
    assert finding.proposed is not None
    assert finding.conflicts
    conflict = finding.conflicts[0]
    assert conflict.overlap_hz > 0
    assert conflict.conflicting_range == (
        existing.allocated_start_hz,
        existing.allocated_end_hz,
    )
    assert conflict.validity_overlap[0] is not None
    assert finding.suggested_gaps, "a refusal without somewhere else to go is a dead end"


def test_a_translated_side_conflict_blocks_even_though_the_uplink_is_clear(world):
    """§8.2 — the case an operator cannot see.

    They choose an uplink centre that is genuinely free, and the downlink image lands on
    somebody else's transmission. Checking only the entered side would accept it.
    """
    config = world["setup"].config
    downlink_leg = config.payload_path.downlink_window_side
    resource = SpectrumResource.objects.get(
        pk=config.spectrum_resources.get(spectrum_resource__leg=downlink_leg).spectrum_resource_id
    )
    downlink_assignment = config.spectrum_assignments.get(frequency_window=config.downlink_window)
    proposal = services.preview(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.OCCUPIED_BW,
        input_value=10 * MHZ,
        rolloff=Decimal("0.2"),
        centre_hz=50 * MHZ,
        valid_from=timezone.now(),
    )
    image = proposal.placement.downlink.allocated
    SpectrumReservation.objects.create(
        spectrum_resource=resource,
        beam_spectrum_assignment=downlink_assignment,
        assignment_start_hz=downlink_assignment.rf_start_hz,
        assignment_end_hz=downlink_assignment.rf_end_hz,
        leg=downlink_leg,
        polarization=config.downlink_window.polarization,
        occupied_start_hz=image.start_hz,
        occupied_end_hz=image.end_hz,
        allocated_start_hz=image.start_hz,
        allocated_end_hz=image.end_hz,
        valid_from=timezone.now(),
        kind="SATNET_PATH",
        satnet_path_id=uuid.uuid4(),
        direction="FWD",
        status=PathStatus.ON_AIR,
    )

    with pytest.raises(services.PathBlockedError) as excinfo:
        _create(world, centre=50 * MHZ)

    conflict_legs = {
        conflict.leg for finding in excinfo.value.findings for conflict in finding.conflicts
    }
    assert downlink_leg in conflict_legs


def test_a_refusal_writes_nothing_at_all(world):
    """§15.6. A half-written allocation is worse than a refused one: rows that committed would
    hold spectrum for a Path that does not exist, and nothing would release them."""
    reserve_range(world["setup"], 48 * MHZ, 52 * MHZ)
    before = SpectrumReservation.objects.count()

    with pytest.raises(services.PathBlockedError):
        _create(world, centre=50 * MHZ)

    assert SatnetPath.objects.count() == 0
    assert SpectrumReservation.objects.count() == before


def test_a_refusal_is_audited(world):
    """§18. A refusal nobody can find afterwards is indistinguishable from a broken button."""
    from audit.models import AuditEvent

    reserve_range(world["setup"], 48 * MHZ, 52 * MHZ)

    with pytest.raises(services.PathBlockedError):
        _create(world, centre=50 * MHZ)

    event = AuditEvent.objects.get(action="SATNET_PATH_BLOCKED")
    assert event.after["findings"][0]["code"] == "SPECTRUM_CONFLICT"


def test_an_allocation_reaching_outside_its_entitlement_is_refused(world):
    """ADR-0019. Guards are part of what is reserved, so a guard at the edge of an assignment
    needs a wider assignment rather than permission to reserve beyond it."""
    with pytest.raises(services.PathBlockedError) as excinfo:
        _create(world, centre=99 * MHZ)

    codes = {f.code for f in excinfo.value.findings}
    assert "OUTSIDE_ENTITLEMENT" in codes


def test_a_period_outside_its_parents_is_refused_for_an_operational_path(world):
    """**OQ-32**, reaching the wizard through `satnets.containment`."""
    world["satnet"].effective_until = timezone.now() + timezone.timedelta(days=5)
    world["satnet"].save()

    with pytest.raises(services.PathBlockedError) as excinfo:
        _create(world, valid_until=timezone.now() + timezone.timedelta(days=400))

    codes = {f.code for f in excinfo.value.findings}
    assert "PERIOD_NOT_CONTAINED" in codes


def test_a_draft_may_be_saved_with_findings_attached(world):
    """**OQ-32**: *"Draft records may temporarily exist outside one or more parent validity
    periods, but they shall produce warnings only."*"""
    world["satnet"].effective_until = timezone.now() + timezone.timedelta(days=5)
    world["satnet"].save()

    path = _create(
        world,
        status=PathStatus.DRAFT,
        valid_until=timezone.now() + timezone.timedelta(days=400),
    )

    assert path.pk is not None
    assert SpectrumReservation.objects.filter(satnet_path_id=path.pk).count() == 0


# ---------------------------------------------------------------------------
# §9.3 — Auto-place proposes and never saves
# ---------------------------------------------------------------------------
def test_auto_place_returns_a_proposal_and_saves_nothing(world):
    proposal = services.auto_place(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.OCCUPIED_BW,
        input_value=10 * MHZ,
        rolloff=Decimal("0.2"),
        valid_from=timezone.now(),
    )

    assert proposal is not None
    assert proposal.ok
    assert SatnetPath.objects.count() == 0
    assert SpectrumReservation.objects.count() == 0


def test_auto_place_is_deterministic(world):
    """§9.3. An operator who reopens the wizard must not be shown a different answer."""
    centres = set()
    for _ in range(5):
        proposal = services.auto_place(
            satnet=world["satnet"],
            direction="FWD",
            input_mode=InputMode.OCCUPIED_BW,
            input_value=10 * MHZ,
            rolloff=Decimal("0.2"),
            valid_from=timezone.now(),
        )
        centres.add(proposal.placement.uplink.occupied.start_hz)

    assert len(centres) == 1


def test_auto_place_avoids_what_is_already_held(world):
    reserve_range(world["setup"], 0, 30 * MHZ)

    proposal = services.auto_place(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.OCCUPIED_BW,
        input_value=10 * MHZ,
        rolloff=Decimal("0.2"),
        valid_from=timezone.now(),
    )

    assert proposal.placement.uplink.allocated.start_hz >= 30 * MHZ
    assert proposal.ok


def test_auto_place_returns_none_when_nothing_fits(world):
    reserve_range(world["setup"], 0, 100 * MHZ)

    proposal = services.auto_place(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.OCCUPIED_BW,
        input_value=10 * MHZ,
        rolloff=Decimal("0.2"),
        valid_from=timezone.now(),
    )

    assert proposal is None


def test_a_proposal_accepted_after_the_gap_was_taken_is_refused_on_save(world):
    """The reason §9.5 says the server repeats every check.

    Auto-place proposed into a gap; somebody else took it while the operator was reading. The
    preview was correct when it was computed and is wrong by the time it is submitted.
    """
    proposal = services.auto_place(
        satnet=world["satnet"],
        direction="FWD",
        input_mode=InputMode.OCCUPIED_BW,
        input_value=10 * MHZ,
        rolloff=Decimal("0.2"),
        valid_from=timezone.now(),
    )
    centre = proposal.placement.uplink.occupied.start_hz + proposal.occupied_bw_hz // 2
    taken = proposal.placement.uplink.allocated
    reserve_range(world["setup"], taken.start_hz, taken.end_hz)

    with pytest.raises(services.PathBlockedError):
        _create(world, centre=centre)


# ---------------------------------------------------------------------------
# Scope still applies
# ---------------------------------------------------------------------------
def test_an_operator_without_grants_cannot_create_a_path(world):
    """**A-17** reaches this far too: the Satnet's Beam and Hub must both be granted."""
    from accounts.constants import Role
    from tests.factories import make_user

    operator = make_user("op-path", roles=[Role.OPERATOR])

    with pytest.raises(services.PathBlockedError) as excinfo:
        services.create(actor=operator, satnet=world["satnet"], values=_values(world))

    assert excinfo.value.findings[0].code == "OUT_OF_SCOPE"
    assert SatnetPath.objects.count() == 0
