"""Bandwidth, edges and the rounding policy. Sections 9.2, 14.2, 14.3 — assumption **A-09**.

The property tests here are the reason this slice was pulled forward. The formulas are
small enough to look obviously right and subtle enough to be wrong at the edges: an odd
bandwidth, a roll-off that does not divide evenly, a value above the 32-bit boundary.
Examples catch the cases someone thought of; properties catch the ones nobody did.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from calculations import bandwidth, rounding
from calculations.ranges import FrequencyRange
from calculations.types import BandwidthRequest, GuardWidths

# Realistic ranges. Symbol rates from a 64 ksps telemetry link to a 500 Msps trunk; the
# centre spans L-band IF through Ka-band so nothing passes by staying under 2^31.
SYMBOL_RATES = st.integers(min_value=1_000, max_value=500_000_000)
ROLLOFFS = st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=4)
CENTRES = st.integers(min_value=1_000_000_000, max_value=30_000_000_000)


# ---------------------------------------------------------------------------
# Occupied bandwidth
# ---------------------------------------------------------------------------
def test_occupied_bandwidth_is_symbol_rate_times_one_plus_rolloff():
    """The Specification Dictionary's own calculation note for OCCUPIED_BANDWIDTH."""
    assert bandwidth.occupied_bandwidth_hz(10_000_000, Decimal("0.35")) == 13_500_000


def test_occupied_bandwidth_rounds_up():
    """**A-09**. Rounding down would claim the transmission occupies less than it does,
    and the platform would allow a neighbour one Hz too close."""
    # 3 x 1.35 = 4.05, which is not a whole Hz.
    assert bandwidth.occupied_bandwidth_hz(3, Decimal("0.35")) == 5


def test_a_zero_rolloff_leaves_the_symbol_rate_unchanged():
    assert bandwidth.occupied_bandwidth_hz(1_000_000, Decimal(0)) == 1_000_000


def test_a_float_rolloff_is_refused():
    """Section 14.1. 0.35 as a float is not thirty-five hundredths, and a bandwidth derived
    from it would be wrong in a way nothing downstream could detect."""
    with pytest.raises(TypeError, match="must be Decimal"):
        bandwidth.occupied_bandwidth_hz(1_000_000, 0.35)  # type: ignore[arg-type]


def test_a_rolloff_entered_as_a_percentage_is_refused():
    """35 is someone typing a percentage. Treated as a factor it would produce a bandwidth
    thirty-six times too wide, with no error anywhere."""
    with pytest.raises(ValueError, match="between 0 and 1"):
        bandwidth.occupied_bandwidth_hz(1_000_000, Decimal(35))


def test_a_ka_band_bandwidth_exceeds_the_32_bit_range():
    """ADR-0003. Anything storing this in an int32 column overflows on the first real
    Ka-band value."""
    assert bandwidth.occupied_bandwidth_hz(2_000_000_000, Decimal("0.2")) > 2**31 - 1


# ---------------------------------------------------------------------------
# Symbol rate — the reverse direction
# ---------------------------------------------------------------------------
def test_symbol_rate_is_occupied_bandwidth_over_one_plus_rolloff():
    assert bandwidth.symbol_rate_sps(13_500_000, Decimal("0.35")) == 10_000_000


def test_symbol_rate_rounds_down():
    """**A-09**, the opposite way from occupied bandwidth. Rounding up would over-state
    what the transmission can carry."""
    # 5 / 1.35 = 3.703..., and the platform must not claim 4.
    assert bandwidth.symbol_rate_sps(5, Decimal("0.35")) == 3


def test_the_round_trip_loses_at_most_one_symbol_per_second():
    """Not an exact inverse, and it cannot be: one direction rounds up and the other down.

    Stating the bound here means a change to the rounding policy fails a test that explains
    the consequence, rather than one that merely goes red.
    """
    for rate in (1_000, 999_999, 45_000_000, 123_456_789):
        occupied = bandwidth.occupied_bandwidth_hz(rate, Decimal("0.35"))
        recovered = bandwidth.symbol_rate_sps(occupied, Decimal("0.35"))

        assert rate - 1 <= recovered <= rate


# ---------------------------------------------------------------------------
# Entry modes — section 9.2
# ---------------------------------------------------------------------------
def test_either_input_completes_the_pair():
    from_rate = bandwidth.resolve_request(
        BandwidthRequest(rolloff=Decimal("0.25"), symbol_rate_sps=8_000_000)
    )
    from_bandwidth = bandwidth.resolve_request(
        BandwidthRequest(rolloff=Decimal("0.25"), occupied_bandwidth_hz=10_000_000)
    )

    assert from_rate == (8_000_000, 10_000_000)
    assert from_bandwidth == (8_000_000, 10_000_000)


def test_the_supplied_value_is_returned_unchanged():
    """Deriving and then re-deriving would round twice and drift away from what was typed."""
    _, occupied = bandwidth.resolve_request(
        BandwidthRequest(rolloff=Decimal("0.35"), occupied_bandwidth_hz=13_500_001)
    )

    assert occupied == 13_500_001


def test_supplying_both_inputs_is_refused():
    """Section 9.2: only one of the two is editable at a time. Accepting both would leave
    the engine guessing which one the operator meant."""
    with pytest.raises(ValueError, match="exactly one"):
        BandwidthRequest(
            rolloff=Decimal("0.35"), symbol_rate_sps=10_000_000, occupied_bandwidth_hz=13_500_000
        )


def test_supplying_neither_input_is_refused():
    with pytest.raises(ValueError, match="exactly one"):
        BandwidthRequest(rolloff=Decimal("0.35"))


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
def test_the_occupied_range_is_centred():
    occupied = bandwidth.occupied_range(29_145_000_000, 10_000_000)

    assert occupied == FrequencyRange(29_140_000_000, 29_150_000_000)


def test_an_odd_bandwidth_widens_rather_than_narrows():
    """**A-09**. Half of 5 Hz is 2.5, and rounding the half-width up gives a 6 Hz range.

    One Hz wider than requested, never one Hz narrower — which is what lets every
    containment check downstream assume the stored range is at least the computed
    bandwidth.
    """
    occupied = bandwidth.occupied_range(1_000, 5)

    assert occupied == FrequencyRange(997, 1_003)
    assert occupied.width_hz == 6


def test_the_allocated_range_adds_both_guards():
    occupied = FrequencyRange(29_140_000_000, 29_150_000_000)
    allocated = bandwidth.allocated_range(occupied, GuardWidths(left_hz=500_000, right_hz=250_000))

    assert allocated == FrequencyRange(29_139_500_000, 29_150_250_000)


# ---------------------------------------------------------------------------
# The whole calculation
# ---------------------------------------------------------------------------
def test_place_produces_a_complete_placement():
    placement = bandwidth.place(
        request=BandwidthRequest(rolloff=Decimal("0.35"), symbol_rate_sps=10_000_000),
        centre_hz=29_145_000_000,
        guards=GuardWidths(left_hz=1_000_000, right_hz=1_000_000),
    )

    assert placement.symbol_rate_sps == 10_000_000
    assert placement.occupied_bandwidth_hz == 13_500_000
    assert placement.occupied == FrequencyRange(29_138_250_000, 29_151_750_000)
    assert placement.allocated == FrequencyRange(29_137_250_000, 29_152_750_000)
    assert placement.allocated_bandwidth_hz == 15_500_000
    assert placement.centre_hz == 29_145_000_000


def test_the_stored_occupied_bandwidth_is_the_range_width():
    """They differ by one Hz for an odd bandwidth, and the range is what gets reserved."""
    placement = bandwidth.place(
        request=BandwidthRequest(rolloff=Decimal(0), occupied_bandwidth_hz=5),
        centre_hz=1_000,
        guards=GuardWidths(left_hz=0, right_hz=0),
    )

    assert placement.occupied_bandwidth_hz == 6
    assert placement.occupied.width_hz == 6


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
@given(SYMBOL_RATES, ROLLOFFS)
def test_occupied_bandwidth_is_never_below_the_exact_product(rate, rolloff):
    """The invariant the whole rounding policy exists to guarantee."""
    exact = rounding.exact_product(rate, Decimal(1) + rolloff)

    assert bandwidth.occupied_bandwidth_hz(rate, rolloff) >= exact


@given(SYMBOL_RATES, ROLLOFFS)
def test_occupied_bandwidth_never_exceeds_the_exact_product_by_a_whole_hertz(rate, rolloff):
    """Outward, but only just. Rounding up by more than one Hz would waste spectrum on
    every allocation the platform ever makes."""
    exact = rounding.exact_product(rate, Decimal(1) + rolloff)

    assert bandwidth.occupied_bandwidth_hz(rate, rolloff) - exact < 1


@given(SYMBOL_RATES, ROLLOFFS)
def test_the_round_trip_never_gains_capability(rate, rolloff):
    """A derived symbol rate is never above the one that produced the bandwidth."""
    occupied = bandwidth.occupied_bandwidth_hz(rate, rolloff)

    assert bandwidth.symbol_rate_sps(occupied, rolloff) <= rate


@given(SYMBOL_RATES, ROLLOFFS, CENTRES)
def test_the_occupied_range_always_covers_the_occupied_bandwidth(rate, rolloff, centre):
    """Never short. Every containment check downstream depends on this."""
    computed = bandwidth.occupied_bandwidth_hz(rate, rolloff)
    occupied = bandwidth.occupied_range(centre, computed)

    assert occupied.width_hz >= computed


@given(SYMBOL_RATES, ROLLOFFS, CENTRES)
def test_the_centre_is_always_recoverable(rate, rolloff, centre):
    """The range is built symmetrically, so its width is even and the midpoint is exact."""
    computed = bandwidth.occupied_bandwidth_hz(rate, rolloff)
    occupied = bandwidth.occupied_range(centre, computed)

    assert occupied.centre_hz == centre


@given(
    SYMBOL_RATES,
    ROLLOFFS,
    CENTRES,
    st.integers(min_value=0, max_value=50_000_000),
    st.integers(min_value=0, max_value=50_000_000),
)
def test_occupied_is_always_inside_allocated(rate, rolloff, centre, left, right):
    """§8.1's reservation is the allocated range; a transmission outside it would be using
    spectrum nobody reserved."""
    placement = bandwidth.place(
        request=BandwidthRequest(rolloff=rolloff, symbol_rate_sps=rate),
        centre_hz=centre,
        guards=GuardWidths(left_hz=left, right_hz=right),
    )

    assert placement.allocated.contains(placement.occupied)
    assert placement.allocated_bandwidth_hz == placement.occupied.width_hz + left + right


@given(SYMBOL_RATES, SYMBOL_RATES, ROLLOFFS)
def test_bandwidth_is_monotonic_in_symbol_rate(lower_rate, higher_rate, rolloff):
    """A faster transmission never occupies less spectrum. Obvious, and exactly the kind of
    thing a rounding change can break at one specific value."""
    if lower_rate > higher_rate:
        lower_rate, higher_rate = higher_rate, lower_rate

    assert bandwidth.occupied_bandwidth_hz(lower_rate, rolloff) <= bandwidth.occupied_bandwidth_hz(
        higher_rate, rolloff
    )


@given(SYMBOL_RATES, ROLLOFFS, ROLLOFFS)
def test_bandwidth_is_monotonic_in_rolloff(rate, lower_rolloff, higher_rolloff):
    if lower_rolloff > higher_rolloff:
        lower_rolloff, higher_rolloff = higher_rolloff, lower_rolloff

    assert bandwidth.occupied_bandwidth_hz(rate, lower_rolloff) <= bandwidth.occupied_bandwidth_hz(
        rate, higher_rolloff
    )
