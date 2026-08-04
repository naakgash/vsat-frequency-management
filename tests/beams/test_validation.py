"""Beam configuration rules. Sections 5.2, 5.3, 5.4 and 26.6.

The rule that matters most is §26.6 — a Beam cannot be activated while an enabled direction
is invalid — and most of these tests exist to establish the *precondition* for it: which
configurations are valid, and which are merely unfinished.
"""

from __future__ import annotations

import pytest

from beams import validation
from beams.constants import ConfigurationState, Direction, ValidationOutcome
from beams.models import Beam, BeamDirectionConfig
from calculations.validation import Severity
from inventory.constants import PolarizationType, SpectrumLeg
from inventory.models import PayloadPolarizationMapping
from tests.beams.factories import configure_direction, make_beam, make_valid_beam
from tests.inventory.factories import (
    make_band,
    make_equipment_profile,
    make_payload_path,
    make_satellite,
)


def codes(report: validation.Report) -> set[str]:
    return {f.code for f in report.findings}


# ---------------------------------------------------------------------------
# Configuration state
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_new_beam_is_incomplete():
    """Both directions exist, enabled and unconfigured. Nothing is wrong yet — it is
    unfinished, which is a different thing."""
    report = validation.validate(make_beam())

    assert report.state is ConfigurationState.INCOMPLETE
    assert not report.is_activatable


@pytest.mark.django_db
def test_a_fully_configured_beam_is_valid():
    report = validation.validate(make_valid_beam())

    assert report.state is ConfigurationState.VALID
    assert report.is_activatable
    assert not report.blocking


@pytest.mark.django_db
def test_incompleteness_outranks_invalidity():
    """A half-configured direction produces rule failures that are only consequences of the
    missing data. Reporting those as "invalid" sends someone hunting for a problem that is
    really just unfinished work.
    """
    beam = make_valid_beam()
    rtn = beam.direction_configs.get(direction=Direction.RTN)
    rtn.is_enabled = True  # enabled but never configured
    rtn.save()

    report = validation.validate(beam)

    assert report.state is ConfigurationState.INCOMPLETE
    assert "DIRECTION_INCOMPLETE" in codes(report)


@pytest.mark.django_db
def test_a_beam_with_every_direction_disabled_is_refused():
    """It would carry no traffic at all, which is not a configuration anyone means."""
    beam = make_valid_beam()
    beam.direction_configs.update(is_enabled=False)

    report = validation.validate(beam)

    assert "NO_ENABLED_DIRECTION" in codes(report)
    assert not report.is_activatable


# ---------------------------------------------------------------------------
# Section 5.4 — an explicitly disabled direction is legitimate
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_single_direction_beam_is_valid_and_says_so():
    """§5.4 makes an explicitly disabled direction a deliberate business case. It is
    allowed, and it is *shown*, because a receive-only Beam nobody realises is receive-only
    is a support call."""
    report = validation.validate(make_valid_beam())

    assert report.is_activatable
    assert "DIRECTION_DISABLED" in codes(report)
    disabled = next(f for f in report.findings if f.code == "DIRECTION_DISABLED")
    assert disabled.severity is Severity.WARNING


# ---------------------------------------------------------------------------
# A-06 — window identity
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_window_that_is_not_the_payload_paths_window_is_refused():
    """**A-06**. Not "contained in" — identical. Narrowing a Beam to a sub-range of a shared
    transponder is OQ-27 and is not supported, so enforcing identity now means that answer
    arrives as a feature rather than as a silent behaviour change."""
    beam = make_valid_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    other = make_payload_path(satellite=beam.satellite, code="PP-OTHER")
    config.uplink_window = other.uplink_window
    config.downlink_window = other.downlink_window
    config.save()

    report = validation.validate(beam)

    assert "UPLINK_WINDOW_NOT_PATH_WINDOW" in codes(report)
    assert "DOWNLINK_WINDOW_NOT_PATH_WINDOW" in codes(report)
    assert not report.is_activatable


@pytest.mark.django_db
def test_the_identity_finding_cites_the_open_question():
    """Someone hitting this needs to know it is a deliberate MVP limit, not a bug."""
    beam = make_valid_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    config.uplink_window = make_payload_path(satellite=beam.satellite, code="PP-X").uplink_window
    config.save()

    finding = next(
        f for f in validation.validate(beam).findings if f.code == "UPLINK_WINDOW_NOT_PATH_WINDOW"
    )

    assert "OQ-27" in finding.reference


# ---------------------------------------------------------------------------
# Chain coherence
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_payload_path_from_another_satellite_is_refused():
    """A Beam cannot translate through another satellite's payload."""
    beam = make_valid_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    elsewhere = make_payload_path(satellite=make_satellite("SAT-ELSEWHERE"), code="PP-ELSE")
    config.payload_path = elsewhere
    config.uplink_window = elsewhere.uplink_window
    config.downlink_window = elsewhere.downlink_window
    config.save()

    assert "PAYLOAD_PATH_WRONG_SATELLITE" in codes(validation.validate(beam))


@pytest.mark.django_db
def test_a_payload_path_running_the_other_direction_is_refused():
    beam = make_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    rtn_path = make_payload_path(satellite=beam.satellite, code="PP-RTN", direction=Direction.RTN)
    config.payload_path = rtn_path
    config.uplink_window = rtn_path.uplink_window
    config.downlink_window = rtn_path.downlink_window
    config.uplink_polarization = PolarizationType.RHCP
    config.downlink_polarization = PolarizationType.RHCP
    config.save()
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)

    assert "PAYLOAD_PATH_WRONG_DIRECTION" in codes(validation.validate(beam))


@pytest.mark.django_db
def test_a_canonical_leg_outside_the_chain_is_refused():
    """The operator would be entering a centre frequency for a leg this direction does not
    use (**A-07**)."""
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        canonical_leg=SpectrumLeg.REMOTE_UPLINK
    )

    assert "CANONICAL_LEG_NOT_IN_CHAIN" in codes(validation.validate(beam))


@pytest.mark.django_db
def test_the_default_canonical_leg_is_part_of_its_chain():
    """**A-07**'s build default, checked rather than assumed."""
    assert "CANONICAL_LEG_NOT_IN_CHAIN" not in codes(validation.validate(make_valid_beam()))


# ---------------------------------------------------------------------------
# Polarization — section 13.7
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_polarization_pair_the_payload_path_does_not_permit_is_refused():
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        downlink_polarization=PolarizationType.LHCP
    )

    assert "POLARIZATION_NOT_PERMITTED" in codes(validation.validate(beam))


@pytest.mark.django_db
def test_a_payload_path_with_no_mappings_warns_rather_than_blocks():
    """Which pairs are permitted is **OQ-03** and nothing is seeded. Treating an empty list
    as "nothing is allowed" would make every Beam un-activatable until an open question is
    answered — a worse failure than proceeding with the gap recorded."""
    beam = make_beam()
    config = configure_direction(beam, Direction.FWD, with_mapping=False)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)
    PayloadPolarizationMapping.objects.filter(payload_path=config.payload_path).delete()

    report = validation.validate(beam)

    assert "NO_POLARIZATION_MAPPINGS" in codes(report)
    assert report.is_activatable


@pytest.mark.django_db
def test_an_unset_polarization_is_refused():
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(uplink_polarization="")

    assert "POLARIZATION_NOT_SET" in codes(validation.validate(beam))


# ---------------------------------------------------------------------------
# Equipment — sections 5.2, 13.5
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_empty_equipment_pool_warns_rather_than_blocks():
    """Profile limits are **OQ-04** and none are seeded, so an empty pool is the expected
    state. A Satnet Path refuses loudly in S11 when it finds nothing to convert through."""
    beam = make_beam()
    configure_direction(beam, Direction.FWD, with_equipment=False)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)

    report = validation.validate(beam)

    assert "NO_EQUIPMENT_PROFILES" in codes(report)
    assert report.is_activatable


@pytest.mark.django_db
def test_an_equipment_profile_from_another_band_is_refused():
    beam = make_valid_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    config.equipment_profiles.all().delete()
    from beams.models import BeamDirectionEquipmentProfile

    BeamDirectionEquipmentProfile.objects.create(
        direction_config=config,
        equipment_profile=make_equipment_profile(band=make_band("BAND-OTHER"), code="BUC-OTHER"),
    )

    assert "EQUIPMENT_WRONG_BAND" in codes(validation.validate(beam))


@pytest.mark.django_db
def test_a_deactivated_equipment_profile_is_refused():
    """Section 20: deactivated inventory cannot be used by a new allocation."""
    beam = make_valid_beam()
    config = beam.direction_configs.get(direction=Direction.FWD)
    entry = config.equipment_profiles.first()
    entry.equipment_profile.is_active = False
    entry.equipment_profile.save()

    assert "EQUIPMENT_INACTIVE" in codes(validation.validate(beam))


# ---------------------------------------------------------------------------
# The report itself
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_problem_is_reported_at_once():
    """The builder shows an administrator everything wrong on one screen."""
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        canonical_leg=SpectrumLeg.REMOTE_UPLINK, downlink_polarization=PolarizationType.LHCP
    )

    report = validation.validate(beam)

    assert {"CANONICAL_LEG_NOT_IN_CHAIN", "POLARIZATION_NOT_PERMITTED"} <= codes(report)


@pytest.mark.django_db
def test_every_finding_cites_where_the_rule_comes_from():
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(uplink_polarization="")

    assert all(f.reference for f in validation.validate(beam).findings)


@pytest.mark.django_db
def test_warnings_alone_still_pass():
    report = validation.validate(make_valid_beam())

    assert report.outcome is ValidationOutcome.PASSED_WITH_WARNINGS
    assert report.is_activatable


@pytest.mark.django_db
def test_a_finding_serialises_to_plain_json():
    """``BeamValidationResult.findings`` has to stay readable after this dataclass changes
    shape, so it stores dictionaries rather than pickled objects."""
    beam = make_valid_beam()
    finding = validation.validate(beam).findings[0]

    assert set(finding.as_dict()) == {"code", "severity", "message", "direction", "reference"}
    assert all(isinstance(v, str) for v in finding.as_dict().values())


# ---------------------------------------------------------------------------
# Section 26.20
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_beams_are_seeded():
    """A Beam is built from Frequency Windows and Payload Paths, and none of those are
    seeded either. A plausible-looking Beam would be indistinguishable from a real one."""
    assert Beam.objects.count() == 0
    assert BeamDirectionConfig.objects.count() == 0
