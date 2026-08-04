"""Spectrum Resources and Beam Spectrum Assignments. **OQ-25** and **OQ-27**, ADR-0018/0019.

The two answers that lifted the S9 gate, expressed as tests.

The rules here are unlike the rest of `beams.validation` in one way worth stating: their
failure mode is **silence**. A Beam with a wrong polarization is refused; a Beam whose leg
maps to no Spectrum Resource is *accepted*, allocates happily, and competes with nothing.
There is no error to see and no missing row to notice. That is why the mapping blocks
activation rather than warning, and why several of these tests assert on a refusal that a
reader might expect to be a warning.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from beams import services, validation
from beams.constants import ConfigurationState, Direction
from beams.models import BeamDirectionSpectrumResource, BeamSpectrumAssignment
from inventory.constants import SpectrumResourceKind
from inventory.models import SpectrumResource
from tests.beams.factories import (
    assign_whole_windows,
    configure_direction,
    make_beam,
    make_valid_beam,
    map_spectrum_resources,
)
from tests.factories import make_admin

pytestmark = pytest.mark.django_db


def _fwd(beam):
    return beam.direction_configs.get(direction=Direction.FWD)


# ---------------------------------------------------------------------------
# OQ-25 — overlap is judged on a resource, and an unmapped leg is not safe
# ---------------------------------------------------------------------------
def test_a_leg_with_no_spectrum_resource_blocks_activation():
    """The rule that replaced "the Beam is the pool".

    Blocking rather than warning is the decision under test. An unmapped leg competes with
    nothing, so every allocation on it is accepted — including one that genuinely collides —
    and no constraint can detect it, because an exclusion constraint only compares rows that
    exist.
    """
    beam = make_beam("BEAM-NORES")
    configure_direction(beam, Direction.FWD, with_resources=False)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)

    report = validation.validate(beam)

    findings = [f for f in report.findings if f.code == "LEG_HAS_NO_SPECTRUM_RESOURCE"]
    assert len(findings) == 2, "both legs of the chain are unmapped, and both must be reported"
    assert all(f.blocks for f in findings)
    assert report.state is ConfigurationState.INVALID


def test_the_unmapped_leg_finding_names_the_leg_and_what_to_supply():
    beam = make_beam("BEAM-NORES2")
    configure_direction(beam, Direction.FWD, with_resources=False)

    finding = next(
        f for f in validation.validate(beam).findings if f.code == "LEG_HAS_NO_SPECTRUM_RESOURCE"
    )

    assert "HUB_UPLINK" in finding.message
    assert "compete" in finding.message
    assert "OQ-25" in finding.reference


def test_mapping_only_one_leg_still_blocks():
    """Half a mapping is not half safe — the unmapped leg is exactly as silent."""
    beam = make_beam("BEAM-HALF")
    config = configure_direction(beam, Direction.FWD, with_resources=False)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)

    uplink_leg = config.payload_path.uplink_window_side
    resource = SpectrumResource.objects.create(
        satellite=beam.satellite,
        code="SR-ONLY-UP",
        name="Uplink only",
        kind=SpectrumResourceKind.PAYLOAD_INPUT,
        leg=uplink_leg,
        effective_from="2026-01-01T00:00:00Z",
    )
    BeamDirectionSpectrumResource.objects.create(
        direction_config=config, spectrum_resource=resource
    )

    findings = [
        f for f in validation.validate(beam).findings if f.code == "LEG_HAS_NO_SPECTRUM_RESOURCE"
    ]

    assert len(findings) == 1
    assert uplink_leg not in findings[0].message


def test_a_resource_from_another_satellite_is_refused():
    beam = make_valid_beam("BEAM-XSAT")
    config = _fwd(beam)
    from tests.inventory.factories import make_satellite

    elsewhere = SpectrumResource.objects.create(
        satellite=make_satellite("SAT-OTHER"),
        code="SR-OTHER",
        name="Elsewhere",
        kind=SpectrumResourceKind.PAYLOAD_INPUT,
        leg=config.payload_path.uplink_window_side,
        effective_from="2026-01-01T00:00:00Z",
    )
    BeamDirectionSpectrumResource.objects.create(
        direction_config=config, spectrum_resource=elsewhere
    )

    codes = {f.code for f in validation.validate(beam).findings}

    assert "SPECTRUM_RESOURCE_WRONG_SATELLITE" in codes


def test_a_deactivated_resource_is_refused():
    """**A-22**: resources expire, and an expired one no longer describes the payload."""
    beam = make_valid_beam("BEAM-DEADRES")
    config = _fwd(beam)
    SpectrumResource.objects.filter(
        pk__in=config.spectrum_resources.values("spectrum_resource_id")
    ).update(is_active=False)

    codes = {f.code for f in validation.validate(beam).findings}

    assert "SPECTRUM_RESOURCE_DEACTIVATED" in codes


def test_a_resource_may_be_shared_by_two_beams():
    """The point of the whole change: two Beams competing on one payload input.

    Under the superseded **A-01** this was impossible to express — two Beams were two pools
    by definition, and the exclusion constraint would have let both allocate the same Hz.
    """
    first = make_valid_beam("BEAM-A")
    second = make_beam("BEAM-B", satellite=first.satellite, band=first.band)
    configure_direction(second, Direction.FWD, with_resources=False)

    shared = map_spectrum_resources(_fwd(first))[0]
    BeamDirectionSpectrumResource.objects.create(
        direction_config=_fwd(second), spectrum_resource=shared
    )

    assert shared.beam_directions.count() == 2
    assert {link.direction_config.beam.code for link in shared.beam_directions.all()} == {
        "BEAM-A",
        "BEAM-B",
    }


def test_polarization_is_a_property_of_the_resource_not_of_the_key():
    """**OQ-25**: polarizations separate only *"where their RF chains are independently
    implemented"*, which is a fact about an installation rather than a rule.

    So a blank polarization is a statement — both polarizations compete here — and the
    property that reads it is named after the question rather than after the column.
    """
    shared = SpectrumResource(polarization="")
    independent = SpectrumResource(polarization="RHCP")

    assert shared.separates_polarizations is False
    assert independent.separates_polarizations is True


# ---------------------------------------------------------------------------
# OQ-27 — the window is a ceiling; assignments are the spectrum
# ---------------------------------------------------------------------------
def test_configuring_a_direction_creates_a_full_window_assignment():
    """The fixed-HTS default, and what makes this change invisible to existing Beams."""
    admin = make_admin()
    beam = make_beam("BEAM-DEFAULT")
    config = configure_direction(beam, Direction.FWD, with_assignments=False)

    services.update_direction(actor=admin, config=config, values={}, reason="Configure")

    assignments = list(config.spectrum_assignments.all())
    assert len(assignments) == 2, "one per window"
    assert all(a.is_whole_window for a in assignments)
    assert all(a.effective_until is None for a in assignments)


def test_the_default_assignment_never_re_widens_a_narrowed_one():
    """The one way this helper could do real harm.

    A direction narrowed to half its window, then saved for an unrelated reason, must not
    silently get the other half back. Filling a gap and editing an existing row look similar
    in code and are not similar at all in consequence.
    """
    admin = make_admin()
    beam = make_valid_beam("BEAM-NARROW")
    config = _fwd(beam)
    uplink = config.uplink_window
    narrowed = config.spectrum_assignments.get(frequency_window=uplink)
    narrowed.rf_end_hz = uplink.rf_start_hz + (uplink.width_hz // 2)
    narrowed.save()

    services.update_direction(actor=admin, config=config, values={"notes": "unrelated"})

    narrowed.refresh_from_db()
    assert narrowed.rf_end_hz == uplink.rf_start_hz + (uplink.width_hz // 2)
    assert config.spectrum_assignments.filter(frequency_window=uplink).count() == 1


def test_a_window_with_no_active_assignment_blocks_activation():
    """A direction that may use nothing should say so, not report an empty gap list."""
    beam = make_valid_beam("BEAM-NOASSIGN")
    config = _fwd(beam)
    config.spectrum_assignments.update(is_active=False)

    report = validation.validate(beam)

    findings = [f for f in report.findings if f.code == "WINDOW_HAS_NO_ACTIVE_ASSIGNMENT"]
    assert len(findings) == 2
    assert report.state is ConfigurationState.INVALID


def test_a_direction_may_hold_several_disjoint_assignments():
    """**OQ-27**: *"one or more sub-ranges"*."""
    beam = make_valid_beam("BEAM-MULTI")
    config = _fwd(beam)
    uplink = config.uplink_window
    midpoint = uplink.rf_start_hz + (uplink.width_hz // 2)

    first = config.spectrum_assignments.get(frequency_window=uplink)
    first.rf_end_hz = midpoint
    first.save()
    BeamSpectrumAssignment.objects.create(
        direction_config=config,
        frequency_window=uplink,
        payload_path=config.payload_path,
        rf_start_hz=midpoint,
        rf_end_hz=uplink.rf_end_hz,
        window_rf_start_hz=uplink.rf_start_hz,
        window_rf_end_hz=uplink.rf_end_hz,
        effective_from=uplink.effective_from,
    )

    assert config.spectrum_assignments.filter(frequency_window=uplink).count() == 2
    assert validation.validate(beam).state is ConfigurationState.VALID


def test_two_active_assignments_may_not_overlap_in_rf_and_time():
    """`excl_assignment_overlap`. Two answers to "what may this Beam use" is not an answer,
    and the gap engine would count the shared spectrum twice."""
    beam = make_valid_beam("BEAM-OVERLAP")
    config = _fwd(beam)
    uplink = config.uplink_window

    with pytest.raises(IntegrityError, match="excl_assignment_overlap"):
        with transaction.atomic():
            BeamSpectrumAssignment.objects.create(
                direction_config=config,
                frequency_window=uplink,
                payload_path=config.payload_path,
                rf_start_hz=uplink.rf_start_hz,
                rf_end_hz=uplink.rf_end_hz,
                window_rf_start_hz=uplink.rf_start_hz,
                window_rf_end_hz=uplink.rf_end_hz,
                effective_from=uplink.effective_from,
            )


def test_assignments_may_overlap_in_rf_when_their_periods_do_not():
    """A payload reconfiguration is a new assignment, not an edit — which is the whole point
    of the period. Same spectrum, consecutive periods, both stored."""
    beam = make_valid_beam("BEAM-SUCCESSION")
    config = _fwd(beam)
    uplink = config.uplink_window
    current = config.spectrum_assignments.get(frequency_window=uplink)
    current.effective_until = "2027-01-01T00:00:00Z"
    current.save()

    successor = BeamSpectrumAssignment.objects.create(
        direction_config=config,
        frequency_window=uplink,
        payload_path=config.payload_path,
        rf_start_hz=uplink.rf_start_hz,
        rf_end_hz=uplink.rf_end_hz,
        window_rf_start_hz=uplink.rf_start_hz,
        window_rf_end_hz=uplink.rf_end_hz,
        effective_from="2027-01-01T00:00:00Z",
    )

    assert successor.pk is not None
    assert config.spectrum_assignments.filter(frequency_window=uplink).count() == 2


def test_an_assignment_cannot_escape_its_window():
    """`ck_assignment_within_window` — the CHECK that makes the Window a ceiling."""
    beam = make_valid_beam("BEAM-ESCAPE")
    config = _fwd(beam)
    uplink = config.uplink_window
    assignment = config.spectrum_assignments.get(frequency_window=uplink)

    with pytest.raises(IntegrityError, match="ck_assignment_within_window"):
        with transaction.atomic():
            assignment.rf_end_hz = uplink.rf_end_hz + 1
            assignment.save()


def test_the_window_edge_copy_cannot_lie():
    """`fk_assignment_window_edges`.

    Without the composite key, widening the *copy* of the window's edges would satisfy
    `ck_assignment_within_window` and let an assignment reach outside the spectrum its window
    actually grants — which is the one thing a Window exists to prevent (§13.2).
    """
    beam = make_valid_beam("BEAM-LIE")
    config = _fwd(beam)
    assignment = config.spectrum_assignments.get(frequency_window=config.uplink_window)

    with pytest.raises(IntegrityError, match="fk_assignment_window_edges"):
        with transaction.atomic():
            assignment.window_rf_end_hz = assignment.window_rf_end_hz + 1_000_000
            assignment.rf_end_hz = assignment.window_rf_end_hz
            assignment.save()


def test_an_assignment_start_must_precede_its_end():
    beam = make_valid_beam("BEAM-BACKWARDS")
    config = _fwd(beam)
    assignment = config.spectrum_assignments.get(frequency_window=config.uplink_window)

    with pytest.raises(IntegrityError, match="ck_assignment_start_lt_end"):
        with transaction.atomic():
            assignment.rf_start_hz = assignment.rf_end_hz
            assignment.save()


# ---------------------------------------------------------------------------
# Nothing is seeded
# ---------------------------------------------------------------------------
def test_no_spectrum_resource_is_seeded():
    """§26.20. Which resources exist and which Beams share them is **OQ-25** data drawn from
    the approved frequency and polarization plan.

    There is no defensible default. One resource per Beam reinstates the superseded A-01
    under a new name; one per satellite forbids all reuse. Both are guesses about
    interference, which is the thing this record exists to replace.
    """
    assert SpectrumResource.objects.count() == 0


def test_no_assignment_exists_without_a_beam_to_carry_it():
    assert BeamSpectrumAssignment.objects.count() == 0


def test_assignments_are_pinned_to_the_payload_path_version():
    """**OQ-27** requires association with a payload-configuration version, and a Payload Path
    version is the versioned record of one (**A-16**)."""
    beam = make_valid_beam("BEAM-PINNED")
    config = _fwd(beam)

    for assignment in config.spectrum_assignments.all():
        assert assignment.payload_path_id == config.payload_path_id


def test_assign_whole_windows_is_idempotent():
    """Called by both the service and the factories; a second call must not duplicate."""
    beam = make_valid_beam("BEAM-IDEM")
    config = _fwd(beam)

    assign_whole_windows(config)
    assign_whole_windows(config)

    assert config.spectrum_assignments.count() == 2
