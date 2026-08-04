"""Two-sided placement. ADR-0006, sections 8.1 and 13.7.

A Satnet Path occupies spectrum on an uplink leg *and* a downlink leg, and §8.1 makes each
exclusive. The claim these tests protect is that one side is the **image** of the other
rather than a second independent calculation — because two independent calculations can
differ by a Hz, and a Hz is enough to make an allocation fit on one side and fail on the
other.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from calculations import bandwidth, validation
from calculations.ranges import FrequencyRange
from calculations.translation import TranslationMethod, TranslationSpec
from calculations.types import BandwidthRequest, GuardWidths

FWD = TranslationSpec(method=TranslationMethod.OFFSET_SUBTRACT, constant_hz=10_000_000_000)
INVERTING = TranslationSpec(method=TranslationMethod.LO_REFLECT, constant_hz=49_000_000_000)


def two_sided(translation=FWD, centre=29_145_000_000, entered="UPLINK", **extra):
    return bandwidth.place_both_sides(
        request=BandwidthRequest(
            rolloff=extra.pop("rolloff", Decimal("0.35")),
            symbol_rate_sps=extra.pop("symbol_rate_sps", 10_000_000),
        ),
        centre_hz=centre,
        guards=GuardWidths(left_hz=1_000_000, right_hz=1_000_000),
        translation=translation,
        entered_side=entered,
    )


# ---------------------------------------------------------------------------
# Both sides
# ---------------------------------------------------------------------------
def test_the_downlink_is_the_image_of_the_uplink():
    placement = two_sided()

    assert placement.uplink.occupied == FrequencyRange(29_138_250_000, 29_151_750_000)
    assert placement.downlink.occupied == FrequencyRange(19_138_250_000, 19_151_750_000)


def test_both_sides_reserve_the_same_width():
    """Translation preserves width, so a disagreement means the two sides were computed
    independently — the failure ADR-0006 exists to make impossible."""
    placement = two_sided()

    assert placement.widths_agree


def test_the_transmission_itself_is_unchanged_between_the_sides():
    """The symbol rate, roll-off, bandwidth and guards describe the transmission, not the
    leg it is observed on. Only its position moves."""
    placement = two_sided()

    assert placement.uplink.symbol_rate_sps == placement.downlink.symbol_rate_sps
    assert placement.uplink.rolloff == placement.downlink.rolloff
    assert placement.uplink.occupied_bandwidth_hz == placement.downlink.occupied_bandwidth_hz
    assert placement.uplink.guards == placement.downlink.guards


def test_an_operator_may_enter_from_the_downlink_side():
    """Section 9.3 lets the canonical entry side differ by direction (OQ-28)."""
    placement = two_sided(centre=19_145_000_000, entered="DOWNLINK")

    assert placement.downlink.centre_hz == 19_145_000_000
    assert placement.uplink.centre_hz == 29_145_000_000
    assert placement.entered_side == "DOWNLINK"


def test_entering_from_either_side_produces_the_same_pair():
    """The pair is symmetric: which side was typed changes nothing about the result."""
    from_uplink = two_sided(centre=29_145_000_000, entered="UPLINK")
    from_downlink = two_sided(centre=19_145_000_000, entered="DOWNLINK")

    assert from_uplink.uplink.occupied == from_downlink.uplink.occupied
    assert from_uplink.downlink.occupied == from_downlink.downlink.occupied


# ---------------------------------------------------------------------------
# Inversion
# ---------------------------------------------------------------------------
def test_an_inverting_path_reverses_the_downlink():
    placement = two_sided(translation=INVERTING)

    assert placement.inverted
    # The uplink's low edge becomes the downlink's high edge.
    assert placement.downlink.occupied.end_hz == INVERTING.constant_hz - (
        placement.uplink.occupied.start_hz
    )


def test_inversion_is_carried_rather_than_derived():
    """It cannot be derived, and that is the point.

    Translation preserves width, so any downlink reachable by a reflection is equally
    reachable by a shift: the pair of intervals contains no evidence of which happened.
    Here two placements with identical geometry differ only in the flag, which is exactly
    why the flag has to come from the Payload Path.
    """
    reflected = two_sided(translation=INVERTING)
    shift = TranslationSpec(
        method=TranslationMethod.OFFSET_SUBTRACT,
        constant_hz=(29_145_000_000 - reflected.downlink.centre_hz),
    )
    shifted = two_sided(translation=shift)

    assert shifted.downlink.occupied == reflected.downlink.occupied
    assert reflected.inverted
    assert not shifted.inverted


def test_an_inverting_path_still_round_trips():
    placement = two_sided(translation=INVERTING, centre=29_145_000_000)

    assert placement.widths_agree
    assert placement.uplink.centre_hz == 29_145_000_000


# ---------------------------------------------------------------------------
# Validation across both legs
# ---------------------------------------------------------------------------
def test_a_placement_fitting_one_window_and_not_the_other_is_refused():
    """The case a single-sided check passes and should not. Both reservations are
    exclusive (§8.1), so both must hold."""
    placement = two_sided()

    findings = validation.check_two_sided(
        placement,
        uplink_window=FrequencyRange(29_000_000_000, 29_500_000_000),
        downlink_window=FrequencyRange(19_000_000_000, 19_100_000_000),
    )

    assert "DOWNLINK_OUTSIDE_WINDOW" in {f.code for f in findings}
    assert not validation.is_placeable(findings)


def test_findings_name_the_leg_they_came_from():
    """ "OUTSIDE_WINDOW" on a two-sided result does not say which window."""
    placement = two_sided()

    findings = validation.check_two_sided(
        placement,
        uplink_window=FrequencyRange(29_000_000_000, 29_100_000_000),
        downlink_window=FrequencyRange(19_000_000_000, 19_100_000_000),
    )

    codes = {f.code for f in findings}
    assert "UPLINK_OUTSIDE_WINDOW" in codes
    assert "DOWNLINK_OUTSIDE_WINDOW" in codes


def test_a_placement_fitting_both_windows_is_placeable():
    placement = two_sided()

    findings = validation.check_two_sided(
        placement,
        uplink_window=FrequencyRange(29_000_000_000, 29_500_000_000),
        downlink_window=FrequencyRange(19_000_000_000, 19_500_000_000),
    )

    assert validation.is_placeable(findings)


def test_sides_that_disagree_on_width_are_refused():
    """Unreachable through ``place_both_sides``, and checked because a ``TwoSidedPlacement``
    can also be rebuilt from stored columns."""
    import dataclasses

    placement = two_sided()
    corrupt = dataclasses.replace(
        placement,
        downlink=dataclasses.replace(
            placement.downlink, allocated=FrequencyRange(19_000_000_000, 19_000_100_000)
        ),
    )

    findings = validation.check_two_sided(corrupt)

    assert "SIDES_DISAGREE" in {f.code for f in findings}
    assert not validation.is_placeable(findings)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
SYMBOL_RATES = st.integers(min_value=1_000, max_value=100_000_000)
ROLLOFFS = st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=4)
CENTRES = st.integers(min_value=20_000_000_000, max_value=29_000_000_000)


@given(SYMBOL_RATES, ROLLOFFS, CENTRES)
def test_both_sides_always_reserve_the_same_width(rate, rolloff, centre):
    placement = bandwidth.place_both_sides(
        request=BandwidthRequest(rolloff=rolloff, symbol_rate_sps=rate),
        centre_hz=centre,
        guards=GuardWidths(left_hz=1_000_000, right_hz=1_000_000),
        translation=FWD,
    )

    assert placement.widths_agree


@given(SYMBOL_RATES, ROLLOFFS, CENTRES)
def test_an_inverting_path_always_preserves_both_widths(rate, rolloff, centre):
    """The case where an independent recomputation would be most likely to drift."""
    placement = bandwidth.place_both_sides(
        request=BandwidthRequest(rolloff=rolloff, symbol_rate_sps=rate),
        centre_hz=centre,
        guards=GuardWidths(left_hz=1_000_000, right_hz=1_000_000),
        translation=TranslationSpec(
            method=TranslationMethod.LO_REFLECT, constant_hz=80_000_000_000
        ),
    )

    assert placement.widths_agree
    assert placement.inverted


@given(SYMBOL_RATES, ROLLOFFS, CENTRES)
def test_occupied_is_always_inside_allocated_on_both_sides(rate, rolloff, centre):
    placement = bandwidth.place_both_sides(
        request=BandwidthRequest(rolloff=rolloff, symbol_rate_sps=rate),
        centre_hz=centre,
        guards=GuardWidths(left_hz=1_000_000, right_hz=1_000_000),
        translation=FWD,
    )

    assert placement.uplink.allocated.contains(placement.uplink.occupied)
    assert placement.downlink.allocated.contains(placement.downlink.occupied)
