"""Placement validation. Sections 12, 13.2, 13.6.

The distinction under test is which findings *block*. Getting that wrong in either
direction is expensive: a warning that should refuse lets an invalid allocation through,
and a refusal that should warn blocks spectrum a Frequency Window explicitly grants.
"""

from __future__ import annotations

from decimal import Decimal

from calculations import bandwidth, validation
from calculations.ranges import FrequencyRange
from calculations.types import BandwidthRequest, GuardWidths
from calculations.validation import Severity


def placement(centre: int = 29_145_000_000, left: int = 1_000_000, right: int = 1_000_000):
    return bandwidth.place(
        request=BandwidthRequest(rolloff=Decimal("0.35"), symbol_rate_sps=10_000_000),
        centre_hz=centre,
        guards=GuardWidths(left_hz=left, right_hz=right),
    )


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# ---------------------------------------------------------------------------
# Window containment — the authoritative check
# ---------------------------------------------------------------------------
def test_a_placement_inside_its_window_reports_nothing_blocking():
    window = FrequencyRange(29_000_000_000, 29_500_000_000)

    findings = validation.check_placement(placement(), window=window)

    assert validation.is_placeable(findings)


def test_a_placement_outside_its_window_is_refused():
    """Section 13.6. A Window is what grants permission to allocate; spectrum outside one
    is not available, so this is an error rather than a warning."""
    window = FrequencyRange(29_000_000_000, 29_100_000_000)

    findings = validation.check_placement(placement(), window=window)

    assert "OUTSIDE_WINDOW" in codes(findings)
    assert not validation.is_placeable(findings)


def test_a_placement_ending_exactly_at_the_window_edge_fits():
    """Both upper edges are exclusive, so they describe the same boundary. This is the case
    a closed-interval implementation gets wrong."""
    place = placement()
    window = FrequencyRange(place.allocated.start_hz, place.allocated.end_hz)

    findings = validation.check_placement(place, window=window)

    assert "OUTSIDE_WINDOW" not in codes(findings)


def test_a_placement_one_hertz_past_the_window_edge_does_not_fit():
    place = placement()
    window = FrequencyRange(place.allocated.start_hz, place.allocated.end_hz - 1)

    findings = validation.check_placement(place, window=window)

    assert "OUTSIDE_WINDOW" in codes(findings)


# ---------------------------------------------------------------------------
# Edge guard — OQ-34
# ---------------------------------------------------------------------------
def test_sitting_inside_the_minimum_edge_guard_warns_rather_than_refuses():
    """**OQ-34** asks whether the edge guard is part of the allocated range or a separate
    standoff. The design's provisional position is *separate*, so this warns.

    Making it an error would answer an open question by implication, which is exactly what
    section 26.20 forbids — and it would do so in code nobody would think to revisit.
    """
    place = placement()
    window = FrequencyRange(place.allocated.start_hz - 100, place.allocated.end_hz + 100)

    findings = validation.check_placement(place, window=window, min_edge_guard_hz=1_000_000)

    assert "INSIDE_EDGE_GUARD" in codes(findings)
    assert validation.is_placeable(findings)
    assert any("OQ-34" in f.reference for f in findings if f.code == "INSIDE_EDGE_GUARD")


def test_clearing_the_edge_guard_reports_nothing():
    place = placement()
    window = FrequencyRange(
        place.allocated.start_hz - 5_000_000, place.allocated.end_hz + 5_000_000
    )

    findings = validation.check_placement(place, window=window, min_edge_guard_hz=1_000_000)

    assert "INSIDE_EDGE_GUARD" not in codes(findings)


def test_the_edge_guard_is_not_checked_once_the_placement_is_already_outside():
    """Two findings for one problem is noise; the containment failure is the actionable
    one."""
    window = FrequencyRange(29_000_000_000, 29_100_000_000)

    findings = validation.check_placement(placement(), window=window, min_edge_guard_hz=1_000)

    assert codes(findings) & {"OUTSIDE_WINDOW", "INSIDE_EDGE_GUARD"} == {"OUTSIDE_WINDOW"}


# ---------------------------------------------------------------------------
# Band limits — informative only
# ---------------------------------------------------------------------------
def test_exceeding_the_band_warns_but_does_not_refuse():
    """Section 13.2 makes Band limits informative and the Frequency Window authoritative.

    Refusing here would let a Band record that is merely out of date block spectrum a
    Window explicitly grants.
    """
    findings = validation.check_placement(
        placement(), band=FrequencyRange(29_000_000_000, 29_100_000_000)
    )

    assert "OUTSIDE_BAND" in codes(findings)
    assert validation.is_placeable(findings)


def test_a_placement_inside_its_band_reports_nothing_about_it():
    findings = validation.check_placement(
        placement(), band=FrequencyRange(27_500_000_000, 30_000_000_000)
    )

    assert "OUTSIDE_BAND" not in codes(findings)


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------
def test_an_unguarded_placement_is_flagged():
    """Adjacency is legal, but a zero guard is usually a missing policy rather than a
    decision — and the values are still an open question (OQ-07)."""
    findings = validation.check_placement(placement(left=0, right=0))

    assert "NO_GUARD_APPLIED" in codes(findings)
    assert validation.is_placeable(findings)


def test_a_guarded_placement_is_not_flagged():
    assert "NO_GUARD_APPLIED" not in codes(validation.check_placement(placement()))


def test_a_reconstructed_placement_whose_reservation_is_too_narrow_is_refused():
    """Unreachable through ``place()``, and checked anyway.

    A ``Placement`` can be rebuilt from stored columns — by the importer, or when
    re-displaying an allocation made under an earlier version of the engine — and a row
    that no longer satisfies the invariant is exactly what someone needs to be told about.
    """
    import dataclasses

    corrupt = dataclasses.replace(
        placement(), allocated=FrequencyRange(29_140_000_000, 29_141_000_000)
    )

    findings = validation.check_placement(corrupt)

    assert "OCCUPIED_OUTSIDE_ALLOCATED" in codes(findings)
    assert not validation.is_placeable(findings)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def test_every_problem_is_reported_at_once_rather_than_the_first():
    """A wizard has to show an operator everything wrong with a placement on one screen.
    Raising on the first problem turns that into four round trips."""
    findings = validation.check_placement(
        placement(left=0, right=0),
        window=FrequencyRange(29_000_000_000, 29_100_000_000),
        band=FrequencyRange(29_000_000_000, 29_100_000_000),
    )

    assert {"NO_GUARD_APPLIED", "OUTSIDE_WINDOW", "OUTSIDE_BAND"} <= codes(findings)


def test_every_finding_cites_where_the_rule_comes_from():
    """An unexplained refusal is one an operator works around."""
    findings = validation.check_placement(
        placement(left=0, right=0), window=FrequencyRange(29_000_000_000, 29_100_000_000)
    )

    assert all(f.reference for f in findings)


def test_blocking_selects_only_the_errors():
    findings = validation.check_placement(
        placement(left=0, right=0), window=FrequencyRange(29_000_000_000, 29_100_000_000)
    )

    assert all(f.severity is Severity.ERROR for f in validation.blocking(findings))
    assert len(validation.blocking(findings)) < len(findings)
