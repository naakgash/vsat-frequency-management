"""Satnet Path validity containment. **OQ-32**, ADR-0020.

The answer, expressed as tests. Three hard containment rules for an operational Path, warnings
only for a draft, and — the part that is easy to miss — *"temporal containment alone is not
sufficient"*: the assignment must also be the Beam's own, on the right direction, at the right
polarization, drawn against the payload path in use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from beams.models import BeamSpectrumAssignment
from calculations.periods import TimePeriod
from satnets import containment, services
from satnets.containment import Limiter
from tests.factories import make_admin
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement

pytestmark = pytest.mark.django_db

JAN = datetime(2026, 1, 1, tzinfo=UTC)


def at(days: int) -> datetime:
    return JAN + timedelta(days=days)


@pytest.fixture
def world():
    """A Satnet on an active Beam, with one assignment — all three periods controllable."""
    setup = make_entitlement(code="CT", start_hz=0, end_hz=100_000_000)
    admin = make_admin()
    from beams import services as beam_services

    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    beam = setup.beam
    beam.effective_from, beam.effective_until = JAN, at(365)
    beam.save()

    assignment = setup.assignment
    assignment.effective_from, assignment.effective_until = JAN, at(365)
    assignment.save()

    hub = make_hub(make_gateway("GW-CT"), "HUB-CT")
    satnet = services.create(
        actor=admin,
        values={
            "code": "SN-CT",
            "name": "Containment",
            "beam": beam,
            "hub": hub,
            "effective_from": JAN,
            "effective_until": at(365),
        },
    )
    return {"satnet": satnet, "beam": beam, "assignment": assignment, "setup": setup}


def _evaluate(world, requested: TimePeriod, *, operational: bool = True, **extra):
    return containment.evaluate(
        satnet=extra.pop("satnet", world["satnet"]),
        beam=extra.pop("beam", world["beam"]),
        assignment=extra.pop("assignment", world["assignment"]),
        requested=requested,
        direction=extra.pop("direction", "FWD"),
        polarization=extra.pop("polarization", "RHCP"),
        operational=operational,
    )


# ---------------------------------------------------------------------------
# The three hard rules
# ---------------------------------------------------------------------------
def test_a_period_inside_all_three_parents_is_accepted(world):
    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert verdict.ok is True
    assert verdict.permitted == TimePeriod(JAN, at(365))


def test_the_maximum_permitted_period_is_the_intersection(world):
    """**OQ-32**: *"the intersection of those three periods"* — and the service returns it, so
    the interface can offer a date rather than making somebody guess."""
    world["satnet"].effective_until = at(200)
    world["satnet"].save()
    world["assignment"].effective_from = at(30)
    world["assignment"].save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert verdict.permitted == TimePeriod(at(30), at(200))


@pytest.mark.parametrize(
    ("parent", "limiter"),
    [("satnet", Limiter.SATNET), ("beam", Limiter.BEAM), ("assignment", Limiter.ASSIGNMENT)],
)
def test_each_parent_can_be_the_limiting_one_and_is_named(world, parent, limiter):
    """*"It shall identify the limiting Satnet, Beam or Spectrum Assignment."*

    An operator told "outside the permitted period" with three candidate causes has to go and
    look at all three. Each of them is also somebody else's to change — a Satnet's dates are
    the operator's, a Beam's belong to engineering, an assignment's to the payload plan.
    """
    record = world[parent]
    record.effective_until = at(50)
    record.save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert verdict.blocks_activation is True
    assert verdict.limiter == limiter
    assert verdict.permitted == TimePeriod(JAN, at(50))
    assert verdict.findings[0].code == "PERIOD_NOT_CONTAINED"


def test_the_message_states_the_cause_and_the_maximum(world):
    """§9.5's shape: what is wrong, what caused it, and what would be accepted."""
    world["beam"].effective_until = at(50)
    world["beam"].save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    message = verdict.findings[0].message
    assert world["beam"].code in message
    assert "2026-02-20" in message  # the maximum permitted end
    assert "maximum permitted period" in message


def test_an_open_ended_request_under_a_bounded_parent_is_refused(world):
    """The case that will happen most: somebody leaves the end date blank meaning "until
    further notice", under a Beam that expires. Left unchecked the allocation outlives the Beam
    it depends on by simply not saying when it stops."""
    verdict = _evaluate(world, TimePeriod(at(10)))

    assert verdict.blocks_activation is True
    assert verdict.limiter == Limiter.SATNET


def test_a_request_starting_before_its_parents_is_refused(world):
    verdict = _evaluate(world, TimePeriod(at(-10), at(100)))

    assert verdict.blocks_activation is True


def test_parents_with_no_common_period_report_that_rather_than_a_maximum(world):
    """A different problem from reaching too far, and it needs a different message: there is no
    period that would be accepted, so offering a maximum would be a lie."""
    world["assignment"].effective_from = at(400)
    world["assignment"].effective_until = at(500)
    world["assignment"].save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert verdict.permitted is None
    assert verdict.findings[0].code == "NO_COMMON_PERIOD"
    assert verdict.findings[0].limiter == Limiter.SATNET  # closes first, at day 365


# ---------------------------------------------------------------------------
# Draft versus operational
# ---------------------------------------------------------------------------
def test_the_same_facts_produce_the_same_findings_for_a_draft(world):
    """*"Draft records may temporarily exist outside one or more parent validity periods, but
    they shall produce warnings only."*

    The severity is the caller's decision; the rules are not. Evaluating drafts differently
    would eventually mean two definitions of "contained", and the one used at activation is
    the one that matters.
    """
    world["beam"].effective_until = at(50)
    world["beam"].save()
    requested = TimePeriod(at(10), at(100))

    draft = _evaluate(world, requested, operational=False)
    operational = _evaluate(world, requested, operational=True)

    assert [f.code for f in draft.findings] == [f.code for f in operational.findings]
    assert draft.permitted == operational.permitted


def test_every_containment_finding_blocks_an_operational_path(world):
    """*"All three containment checks become mandatory before the record can enter an active
    state."*"""
    world["satnet"].effective_until = at(50)
    world["satnet"].save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert verdict.blocks_activation is True


# ---------------------------------------------------------------------------
# "Temporal containment alone is not sufficient"
# ---------------------------------------------------------------------------
def test_an_assignment_belonging_to_another_beam_is_refused(world):
    """The check that stops every date being correct and the spectrum being somebody else's."""
    other = make_entitlement(code="OTHERBEAM")

    verdict = _evaluate(world, TimePeriod(at(10), at(100)), assignment=other.assignment)

    codes = {f.code for f in verdict.findings}
    assert "ASSIGNMENT_WRONG_BEAM" in codes
    assert verdict.blocks_activation is True


def test_a_beam_that_is_not_the_satnets_is_refused(world):
    other = make_entitlement(code="NOTMINE")

    verdict = _evaluate(
        world, TimePeriod(at(10), at(100)), beam=other.beam, assignment=other.assignment
    )

    assert "BEAM_NOT_THE_SATNETS" in {f.code for f in verdict.findings}


def test_an_assignment_on_the_other_direction_is_refused(world):
    verdict = _evaluate(world, TimePeriod(at(10), at(100)), direction="RTN")

    assert "ASSIGNMENT_WRONG_DIRECTION" in {f.code for f in verdict.findings}


def test_an_assignment_at_the_other_polarization_is_refused(world):
    """§25: two polarizations on one leg are two windows, so they are two assignments."""
    verdict = _evaluate(world, TimePeriod(at(10), at(100)), polarization="LHCP")

    assert "ASSIGNMENT_WRONG_POLARIZATION" in {f.code for f in verdict.findings}


def test_an_assignment_drawn_against_a_superseded_payload_path_is_refused(world):
    """*"A new revision is required when the assignment, Beam or relevant payload configuration
    changes."* The assignment records which payload configuration it was drawn against; if the
    direction has moved on, the assignment is stale."""
    other = make_entitlement(code="STALEPP")
    assignment = world["assignment"]
    assignment.payload_path = other.config.payload_path
    assignment.save()

    verdict = _evaluate(world, TimePeriod(at(10), at(100)))

    assert "ASSIGNMENT_STALE_PAYLOAD_PATH" in {f.code for f in verdict.findings}


def test_compatibility_is_checked_even_when_the_period_is_fine(world):
    """The failure this guards against is a Path whose dates are all correct.

    Temporal containment passing is exactly when a compatibility bug would go unnoticed.
    """
    verdict = _evaluate(world, TimePeriod(at(10), at(100)), direction="RTN")

    assert verdict.permitted is not None
    assert verdict.blocks_activation is True


# ---------------------------------------------------------------------------
# The Beam's own validity
# ---------------------------------------------------------------------------
def test_a_beam_now_has_a_validity_period(world):
    """The gap the OQ-32 answer exposed. Beam carried `is_active` and an activation record and
    nothing temporal, although `docs/design/02` had listed it among the effective-dated
    entities since the design pass."""
    assert world["beam"].validity == TimePeriod(JAN, at(365))


def test_a_beam_validity_period_must_run_forwards(world):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError, match="ck_beam_effective_period"):
        with transaction.atomic():
            world["beam"].effective_until = world["beam"].effective_from - timedelta(days=1)
            world["beam"].save()


def test_beam_validity_is_separate_from_activation(world):
    """Both are needed, and they answer different questions. `is_active` is a switch somebody
    flips now; validity is the span over which the Beam is a real thing. An allocation must
    respect both, and a Beam can be inside its validity and switched off."""
    from accounts.models import User
    from beams import services as beam_services

    # Reuse the admin the fixture already made: make_admin() takes a fixed username.
    admin = User.objects.get(username="an-admin")
    beam_services.set_active(actor=admin, beam=world["beam"], active=False)
    world["beam"].refresh_from_db()

    assert world["beam"].is_active is False
    assert world["beam"].validity.contains(TimePeriod(at(10), at(100))) is True


def test_a_new_beam_defaults_to_valid_from_now_and_open_ended():
    """A Beam created without dates is valid from creation, indefinitely — the same default the
    rest of the product's effective-dated records use, so nobody has to fill in a date to
    express "no end planned"."""
    from tests.beams.factories import make_beam

    beam = make_beam("BEAM-DEFAULTS")

    assert beam.effective_until is None
    assert beam.effective_from <= timezone.now()


# ---------------------------------------------------------------------------
# Revisions reference exactly one assignment
# ---------------------------------------------------------------------------
def test_a_second_assignment_in_a_later_period_is_a_separate_entitlement(world):
    """*"A logical Satnet Path may remain in existence while its Beam Spectrum Assignment
    changes… Each revision must reference one Spectrum Assignment."*

    The model already supports the succession — S9a's exclusion constraint permits the same
    spectrum in consecutive periods — and this pins that a Path evaluated against the successor
    gets the successor's bounds, not the union of both.
    """
    world["assignment"].effective_until = at(180)
    world["assignment"].save()
    successor = BeamSpectrumAssignment.objects.create(
        direction_config=world["setup"].config,
        frequency_window=world["setup"].config.uplink_window,
        payload_path=world["setup"].config.payload_path,
        rf_start_hz=0,
        rf_end_hz=100_000_000,
        window_rf_start_hz=0,
        window_rf_end_hz=100_000_000,
        effective_from=at(180),
        effective_until=at(365),
    )

    first = _evaluate(world, TimePeriod(at(10), at(100)))
    second = _evaluate(world, TimePeriod(at(200), at(300)), assignment=successor)

    assert first.permitted == TimePeriod(JAN, at(180))
    assert second.permitted == TimePeriod(at(180), at(365))
    assert first.ok and second.ok


def test_a_path_spanning_two_assignments_is_refused(world):
    """The reason revisions exist at all: one revision, one assignment. A period straddling the
    handover is outside whichever one it leaves."""
    world["assignment"].effective_until = at(180)
    world["assignment"].save()

    verdict = _evaluate(world, TimePeriod(at(100), at(250)))

    assert verdict.blocks_activation is True
    assert verdict.limiter == Limiter.ASSIGNMENT
    assert verdict.permitted == TimePeriod(JAN, at(180))
