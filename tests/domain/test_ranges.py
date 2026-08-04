"""Half-open frequency intervals. Sections 8.4 and 25, assumption **A-10**.

The convention is the whole point: ``[start, end)`` is what makes adjacency decidable
without a tolerance. Most of these tests are about the boundary, because the boundary is
the only place the convention is observable.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from calculations.ranges import EmptyRangeError, FrequencyRange, any_overlap, sort_by_start

# Realistic RF: L-band IF through Ka-band, in Hz. Deliberately spans the 32-bit boundary
# so a range that only works below 2.147 GHz cannot pass (ADR-0003).
HZ = st.integers(min_value=0, max_value=40_000_000_000)


@st.composite
def ranges(draw, min_width: int = 1) -> FrequencyRange:
    start = draw(HZ)
    width = draw(st.integers(min_value=min_width, max_value=2_000_000_000))
    return FrequencyRange(start, start + width)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def test_a_range_carries_its_width():
    assert FrequencyRange(29_000_000_000, 29_500_000_000).width_hz == 500_000_000


def test_an_empty_range_is_refused():
    """An empty interval overlaps nothing, so it would pass every collision check ever
    applied to it — a reservation that silently conflicts with nothing at all."""
    with pytest.raises(EmptyRangeError):
        FrequencyRange(29_000_000_000, 29_000_000_000)


def test_an_inverted_range_is_refused():
    with pytest.raises(EmptyRangeError):
        FrequencyRange(29_500_000_000, 29_000_000_000)


def test_a_float_edge_is_refused():
    """Section 14.1. A float edge is how binary floating point re-enters the path."""
    with pytest.raises(TypeError):
        FrequencyRange(29_000_000_000.0, 29_500_000_000)


def test_a_range_is_immutable():
    """A placement that could be widened after validation is one whose approval means
    nothing."""
    window = FrequencyRange(0, 100)

    with pytest.raises(AttributeError):
        window.start_hz = 50  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The half-open boundary
# ---------------------------------------------------------------------------
def test_the_lower_edge_belongs_to_the_range_and_the_upper_edge_does_not():
    window = FrequencyRange(100, 200)

    assert window.contains_hz(100)
    assert window.contains_hz(199)
    assert not window.contains_hz(200)
    assert not window.contains_hz(99)


def test_touching_ranges_do_not_overlap():
    """Section 25: adjacency is legal. This is the property that makes it decidable."""
    lower = FrequencyRange(0, 100)
    upper = FrequencyRange(100, 200)

    assert not lower.overlaps(upper)
    assert not upper.overlaps(lower)
    assert lower.is_adjacent_to(upper)
    assert lower.gap_to(upper) == 0


def test_ranges_sharing_one_hertz_do_overlap():
    assert FrequencyRange(0, 101).overlaps(FrequencyRange(100, 200))


def test_a_range_contains_one_that_ends_where_it_ends():
    """Both upper edges are exclusive, so they describe the same boundary."""
    assert FrequencyRange(0, 200).contains(FrequencyRange(100, 200))


def test_a_range_does_not_contain_one_that_ends_one_hertz_later():
    assert not FrequencyRange(0, 200).contains(FrequencyRange(100, 201))


def test_the_gap_between_separated_ranges_is_reported():
    assert FrequencyRange(0, 100).gap_to(FrequencyRange(150, 200)) == 50


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------
def test_shifting_preserves_width():
    assert FrequencyRange(100, 200).shift(1_000).width_hz == 100


def test_reflection_preserves_width_and_reverses_position():
    """**A-10**. An inverting translation maps ``[a, b)`` to ``[K-b, K-a)``.

    The exact image is ``(K-b, K-a]`` — reflection swaps which edge is closed — and the
    platform re-normalises it to half-open the other way. Width survives exactly; what
    moves is which single Hz sits on the boundary.
    """
    reflected = FrequencyRange(100, 300).reflect(1_000)

    assert reflected == FrequencyRange(700, 900)
    assert reflected.width_hz == 200


def test_reflecting_twice_returns_the_original():
    """The property that proves the re-normalisation is consistent rather than lossy."""
    original = FrequencyRange(29_000_000_000, 29_500_000_000)

    assert original.reflect(60_000_000_000).reflect(60_000_000_000) == original


def test_expanding_applies_a_guard_to_each_side():
    widened = FrequencyRange(1_000, 2_000).expanded(left_hz=100, right_hz=250)

    assert widened == FrequencyRange(900, 2_250)


def test_a_negative_guard_is_refused():
    """Narrowing an allocation below its occupied bandwidth would reserve less spectrum
    than the transmission actually uses."""
    with pytest.raises(ValueError, match="cannot be negative"):
        FrequencyRange(1_000, 2_000).expanded(left_hz=-1)


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------
def test_ranges_sort_into_spectrum_order():
    unsorted = [FrequencyRange(300, 400), FrequencyRange(100, 200), FrequencyRange(100, 150)]

    assert sort_by_start(unsorted) == [
        FrequencyRange(100, 150),
        FrequencyRange(100, 200),
        FrequencyRange(300, 400),
    ]


def test_the_first_overlapping_pair_is_found():
    found = any_overlap(
        [FrequencyRange(0, 100), FrequencyRange(200, 300), FrequencyRange(250, 400)]
    )

    assert found == (FrequencyRange(200, 300), FrequencyRange(250, 400))


def test_adjacent_ranges_report_no_overlap():
    assert any_overlap([FrequencyRange(0, 100), FrequencyRange(100, 200)]) is None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
@given(ranges(), st.integers(min_value=-(10**12), max_value=10**12))
def test_shifting_always_preserves_width(window, delta):
    assert window.shift(delta).width_hz == window.width_hz


@given(ranges(), st.integers(min_value=0, max_value=10**12))
def test_reflection_always_preserves_width(window, constant):
    assume(constant > window.end_hz)  # keep the image non-negative

    assert window.reflect(constant).width_hz == window.width_hz


@given(ranges(), st.integers(min_value=0, max_value=10**12))
def test_reflection_is_always_its_own_inverse(window, constant):
    assume(constant > window.end_hz)

    assert window.reflect(constant).reflect(constant) == window


@given(ranges(), ranges())
def test_overlap_is_always_symmetric(left, right):
    assert left.overlaps(right) == right.overlaps(left)


@given(ranges(), ranges())
def test_overlapping_and_gapped_are_always_mutually_exclusive(left, right):
    """Two ranges either share spectrum or have a gap between them. Never both, and — with
    a zero gap meaning adjacency — never neither."""
    if left.overlaps(right):
        assert left.gap_to(right) == 0
    else:
        assert left.gap_to(right) >= 0


@given(ranges())
def test_a_range_always_contains_itself(window):
    assert window.contains(window)


@given(
    ranges(), st.integers(min_value=0, max_value=10**9), st.integers(min_value=0, max_value=10**9)
)
def test_expanding_always_contains_the_original(window, left, right):
    assert window.expanded(left_hz=left, right_hz=right).contains(window)
