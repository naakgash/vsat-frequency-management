"""Free capacity. §16, ADR-0009.

Pure arithmetic, tested without a database — the reason `calculations` is kept free of Django
at all. The properties at the bottom are the ones that matter: an example test proves the
engine handles the case somebody thought of, and free-capacity bugs are almost always in the
case nobody did.
"""

from __future__ import annotations

import itertools

from hypothesis import assume, given
from hypothesis import strategies as st

from calculations import gaps
from calculations.ranges import FrequencyRange

HZ = st.integers(min_value=0, max_value=40_000_000_000)


def _range(start: int, end: int) -> FrequencyRange:
    return FrequencyRange(start, end)


def ranges() -> st.SearchStrategy[FrequencyRange]:
    return st.tuples(HZ, HZ).filter(lambda pair: pair[0] < pair[1]).map(lambda p: _range(*p))


# ---------------------------------------------------------------------------
# subtract
# ---------------------------------------------------------------------------
def test_an_empty_entitlement_is_entirely_free():
    assert gaps.subtract(_range(0, 100), []) == [_range(0, 100)]


def test_a_fully_occupied_entitlement_has_no_gaps():
    assert gaps.subtract(_range(0, 100), [_range(0, 100)]) == []


def test_a_reservation_in_the_middle_leaves_two_gaps():
    assert gaps.subtract(_range(0, 100), [_range(40, 60)]) == [_range(0, 40), _range(60, 100)]


def test_touching_reservations_leave_no_gap_between_them():
    """**A-11**. Half-open ranges make adjacency arithmetic rather than a special case: a
    zero-width gap is not a gap, and it must not be reported as one."""
    assert gaps.subtract(_range(0, 100), [_range(0, 50), _range(50, 100)]) == []


def test_overlapping_reservations_are_not_double_counted():
    """The database forbids two reserving rows from overlapping, but this engine is also fed
    by the *proposal* path, where a candidate is compared against what exists. It must not
    subtract the shared spectrum twice and invent a gap."""
    assert gaps.subtract(_range(0, 100), [_range(10, 60), _range(40, 80)]) == [
        _range(0, 10),
        _range(80, 100),
    ]


def test_reservations_outside_the_entitlement_are_ignored():
    """The selector fetches by *resource*, not by assignment (ADR-0018), so a reservation on
    a shared resource but outside this Beam's sub-range will legitimately arrive here."""
    assert gaps.subtract(_range(100, 200), [_range(0, 50), _range(300, 400)]) == [_range(100, 200)]


def test_a_reservation_straddling_the_lower_edge_is_clipped():
    assert gaps.subtract(_range(100, 200), [_range(50, 120)]) == [_range(120, 200)]


def test_reservations_are_sorted_before_subtraction():
    """Callers pass whatever the queryset ordering gave them."""
    assert gaps.subtract(_range(0, 100), [_range(70, 80), _range(20, 30)]) == [
        _range(0, 20),
        _range(30, 70),
        _range(80, 100),
    ]


# ---------------------------------------------------------------------------
# find and summarise
# ---------------------------------------------------------------------------
def test_gaps_are_found_within_each_entitlement_and_never_merged_across_two():
    """Two adjacent assignments are two separate grants (ADR-0019).

    Merging them would offer a transmission spanning the join — which would sit partly
    outside whichever assignment expires first, and the platform would have proposed it.
    """
    found = gaps.find([_range(0, 100), _range(100, 200)], [])

    assert [gap.range for gap in found] == [_range(0, 100), _range(100, 200)]


def test_a_gap_reports_what_it_sits_between():
    """§9.5 wants the neighbours named. "There is room" is less useful than "there is room,
    and you would be next to this"."""
    found = gaps.find([_range(0, 100)], [_range(20, 30), _range(60, 70)])

    middle = next(gap for gap in found if gap.range == _range(30, 60))
    assert middle.below_hz == 30
    assert middle.above_hz == 60


def test_gaps_narrower_than_the_minimum_are_not_offered():
    found = gaps.find([_range(0, 100)], [_range(10, 20), _range(21, 90)], minimum_width_hz=5)

    assert [gap.range for gap in found] == [_range(0, 10), _range(90, 100)]


def test_capacity_totals_the_entitlements_not_the_span_between_them():
    """A Beam entitled to two 20 MHz sub-ranges 200 MHz apart has 40 MHz, not 240 MHz.

    Anything else reports utilisation against spectrum the Beam may not use — and reports it
    as a reassuringly low percentage.
    """
    summary = gaps.summarise([_range(0, 20), _range(200, 220)], [])

    assert summary.total_hz == 40
    assert summary.free_hz == 40
    assert summary.utilisation == 0.0


def test_utilisation_and_largest_gap():
    summary = gaps.summarise([_range(0, 100)], [_range(0, 25)])

    assert summary.used_hz == 25
    assert summary.free_hz == 75
    assert summary.largest_gap_hz == 75
    assert summary.utilisation == 0.25


def test_an_entitlement_of_nothing_reports_no_capacity_rather_than_dividing_by_zero():
    """A direction whose assignment expired is entitled to nothing, and the honest answer is
    zero — not a crash, and not "the whole window"."""
    summary = gaps.summarise([], [])

    assert summary.total_hz == 0
    assert summary.utilisation == 0.0
    assert summary.gaps == ()


# ---------------------------------------------------------------------------
# first_fit
# ---------------------------------------------------------------------------
def test_first_fit_takes_the_lowest_gap_that_is_wide_enough():
    found = gaps.first_fit([_range(0, 100)], [_range(10, 20), _range(30, 40)], width_hz=15)

    assert found is not None
    assert found.range == _range(40, 100)


def test_first_fit_returns_none_when_nothing_fits():
    assert gaps.first_fit([_range(0, 100)], [_range(0, 95)], width_hz=10) is None


def test_first_fit_is_deterministic():
    """§9.3: Auto-place proposes and never saves, so the same inputs must always produce the
    same proposal — an operator who re-opens the wizard should not be shown a different
    answer."""
    entitlements = [_range(0, 500), _range(600, 900)]
    occupied = [_range(100, 200), _range(650, 700)]

    results = {gaps.first_fit(entitlements, occupied, 50).range for _ in range(10)}

    assert len(results) == 1


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=8))
def test_free_spectrum_never_exceeds_the_entitlement(entitlement, occupied):
    free = gaps.subtract(entitlement, occupied)

    assert sum(part.width_hz for part in free) <= entitlement.width_hz


@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=8))
def test_every_free_part_lies_inside_the_entitlement(entitlement, occupied):
    """The property that matters most: the engine must never offer spectrum outside the
    entitlement it was given, because the caller will hand that straight to an operator."""
    for part in gaps.subtract(entitlement, occupied):
        assert part.start_hz >= entitlement.start_hz
        assert part.end_hz <= entitlement.end_hz


@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=8))
def test_no_free_part_overlaps_anything_occupied(entitlement, occupied):
    """The guarantee restated as arithmetic: what the engine calls free is genuinely free."""
    for part in gaps.subtract(entitlement, occupied):
        for taken in occupied:
            assert not part.overlaps(taken)


@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=8))
def test_free_parts_are_disjoint_and_ascending(entitlement, occupied):
    free = gaps.subtract(entitlement, occupied)

    for earlier, later in itertools.pairwise(free):
        assert earlier.end_hz < later.start_hz, "adjacent parts should have been one part"


@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=8))
def test_no_free_part_is_empty(entitlement, occupied):
    """A zero-width gap is not a gap. Reporting one would put "0 Hz available" in a list of
    places an operator could put a transmission."""
    for part in gaps.subtract(entitlement, occupied):
        assert part.width_hz > 0


@given(entitlement=ranges(), occupied=st.lists(ranges(), max_size=6))
def test_subtracting_twice_changes_nothing(entitlement, occupied):
    """Idempotence. The free parts are, by construction, already free."""
    once = gaps.subtract(entitlement, occupied)
    twice = [part for free in once for part in gaps.subtract(free, occupied)]

    assert once == twice


@given(entitlement=ranges(), width=st.integers(min_value=1, max_value=1_000_000))
def test_a_gap_that_first_fit_returns_is_always_wide_enough(entitlement, width):
    assume(entitlement.width_hz >= width)
    found = gaps.first_fit([entitlement], [], width)

    assert found is not None
    assert found.width_hz >= width
