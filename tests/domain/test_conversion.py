"""RF ↔ IF conversion and equipment matching. Specification section 13.5.

The case worth the most attention is ``IF = |RF - LO|``, which is not invertible on its
own. ``sideband`` is what makes it a function in both directions, and several of these
tests exist to prove the two are held together rather than merely stored side by side.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from calculations import validation
from calculations.conversion import (
    ConversionError,
    ConversionMethod,
    EquipmentProfileSpec,
    Sideband,
    best_profile,
    match_profiles,
    to_intermediate,
    to_radio,
)
from calculations.ranges import FrequencyRange

L_BAND = FrequencyRange(950_000_000, 2_000_000_000)
KA_UPLINK = FrequencyRange(29_000_000_000, 30_000_000_000)


def buc(code: str = "BUC-1", **extra) -> EquipmentProfileSpec:
    """A low-side up-converter. RF = LO + IF, non-inverting."""
    return EquipmentProfileSpec(
        code=code,
        method=extra.pop("method", ConversionMethod.LO_PLUS_IF),
        sideband=extra.pop("sideband", Sideband.LOW_SIDE),
        lo_hz=extra.pop("lo_hz", 28_050_000_000),
        rf_limits=extra.pop("rf_limits", KA_UPLINK),
        if_limits=extra.pop("if_limits", L_BAND),
        **extra,
    )


def high_side(code: str = "LNB-1", **extra) -> EquipmentProfileSpec:
    """A high-side down-converter. IF = LO - RF, inverting."""
    return EquipmentProfileSpec(
        code=code,
        method=ConversionMethod.LO_MINUS_IF,
        sideband=Sideband.HIGH_SIDE,
        lo_hz=extra.pop("lo_hz", 21_200_000_000),
        rf_limits=extra.pop("rf_limits", FrequencyRange(19_200_000_000, 20_200_000_000)),
        if_limits=extra.pop("if_limits", L_BAND),
        **extra,
    )


# ---------------------------------------------------------------------------
# The sideband pairing
# ---------------------------------------------------------------------------
def test_a_method_and_sideband_that_disagree_are_refused():
    """The same rule as ck_equipment_conversion_sideband, enforced here too because the
    engine is reachable from the importer, which never sees that constraint until commit."""
    with pytest.raises(ValueError, match="implies"):
        buc(sideband=Sideband.HIGH_SIDE)


def test_a_fixed_offset_accepts_either_sideband():
    """A fixed offset says nothing about where the local oscillator sits."""
    for sideband in Sideband:
        profile = buc(method=ConversionMethod.FIXED_OFFSET, sideband=sideband)

        assert profile.method is ConversionMethod.FIXED_OFFSET


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def test_low_side_injection_shifts_down_to_the_intermediate_frequency():
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)

    assert to_intermediate(rf, buc()) == FrequencyRange(950_000_000, 1_450_000_000)


def test_low_side_injection_shifts_back_up():
    intermediate = FrequencyRange(950_000_000, 1_450_000_000)

    assert to_radio(intermediate, buc()) == FrequencyRange(29_000_000_000, 29_500_000_000)


def test_high_side_injection_reflects_and_therefore_inverts():
    """``IF = LO - RF``. The RF low edge becomes the IF high edge, and width survives."""
    rf = FrequencyRange(19_500_000_000, 19_800_000_000)

    intermediate = to_intermediate(rf, high_side())

    assert intermediate == FrequencyRange(1_400_000_000, 1_700_000_000)
    assert intermediate.width_hz == rf.width_hz


def test_high_side_conversion_is_its_own_inverse():
    """``RF = LO - IF`` and ``IF = LO - RF`` are one map, so there is no second constant to
    get wrong."""
    rf = FrequencyRange(19_500_000_000, 19_800_000_000)
    profile = high_side()

    assert to_radio(to_intermediate(rf, profile), profile) == rf


def test_a_fixed_offset_converts_by_its_offset_rather_than_its_lo():
    profile = buc(
        method=ConversionMethod.FIXED_OFFSET,
        sideband=Sideband.LOW_SIDE,
        lo_hz=28_050_000_000,
        offset_hz=28_000_000_000,
    )
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)

    assert to_intermediate(rf, profile) == FrequencyRange(1_000_000_000, 1_500_000_000)


def test_a_conversion_producing_a_negative_frequency_is_refused():
    """Clear message rather than a negative frequency that fails a range constructor three
    calls later."""
    with pytest.raises(ConversionError, match="below 0 Hz"):
        to_intermediate(FrequencyRange(1_000_000, 2_000_000), buc())


def test_reflecting_through_an_lo_below_the_interval_is_refused():
    """High-side injection means the LO sits above the RF. A profile applied to spectrum
    above its own LO is being applied to spectrum it cannot serve."""
    with pytest.raises(ConversionError, match="requires the local oscillator"):
        to_intermediate(FrequencyRange(22_000_000_000, 22_100_000_000), high_side())


def test_an_inverting_profile_reports_that_it_inverts():
    assert high_side().inverts
    assert not buc().inverts


# ---------------------------------------------------------------------------
# Matching — section 13.5
# ---------------------------------------------------------------------------
def test_a_profile_whose_rf_limits_exclude_the_transmission_is_rejected():
    rf = FrequencyRange(31_000_000_000, 31_100_000_000)

    match = match_profiles(rf, [buc()])[0]

    assert not match.is_usable
    assert "outside the profile's RF limits" in match.rejected_because


def test_a_profile_whose_if_limits_exclude_the_result_is_rejected():
    """The RF fits and the IF does not — the case a check on RF alone would pass."""
    profile = buc(if_limits=FrequencyRange(950_000_000, 1_000_000_000))
    rf = FrequencyRange(29_400_000_000, 29_500_000_000)

    match = match_profiles(rf, [profile])[0]

    assert not match.is_usable
    assert "outside the profile's IF limits" in match.rejected_because


def test_containment_is_of_the_whole_interval_not_its_centre():
    """A transmission whose edge falls outside a profile's range is not carried by it, even
    if its centre is comfortably inside."""
    profile = buc(rf_limits=FrequencyRange(29_000_000_000, 29_500_000_000))
    straddling = FrequencyRange(29_400_000_000, 29_600_000_000)

    assert not match_profiles(straddling, [profile])[0].is_usable


def test_usable_profiles_sort_before_rejected_ones():
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    unusable = buc("BUC-BAD", rf_limits=FrequencyRange(10_000_000_000, 11_000_000_000))

    ordered = match_profiles(rf, [unusable, buc("BUC-OK")])

    assert [m.profile.code for m in ordered] == ["BUC-OK", "BUC-BAD"]


def test_profiles_are_ordered_by_priority():
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    profiles = [buc("BUC-C", priority=300), buc("BUC-A", priority=100), buc("BUC-B", priority=200)]

    ordered = match_profiles(rf, profiles)

    assert [m.profile.code for m in ordered] == ["BUC-A", "BUC-B", "BUC-C"]


def test_equal_priorities_break_deterministically_on_code():
    """Two profiles at the same priority must not swap places between runs, or the same
    request would select different equipment on Tuesday than on Monday."""
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    profiles = [buc("BUC-Z"), buc("BUC-A"), buc("BUC-M")]

    first = [m.profile.code for m in match_profiles(rf, profiles)]
    reordered = [m.profile.code for m in match_profiles(rf, list(reversed(profiles)))]

    assert first == reordered == ["BUC-A", "BUC-M", "BUC-Z"]


def test_the_label_never_affects_selection():
    """Section 13.5: LOW / MID / HIGH are free-text labels and must drive no branching."""
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    profiles = [buc("BUC-1", label="HIGH"), buc("BUC-2", label="LOW")]

    ordered = match_profiles(rf, profiles)

    assert [m.profile.code for m in ordered] == ["BUC-1", "BUC-2"]


def test_the_best_profile_is_the_highest_priority_usable_one():
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    unusable = buc("BUC-FIRST", priority=1, rf_limits=FrequencyRange(1_000, 2_000))

    best = best_profile(rf, [unusable, buc("BUC-SECOND", priority=50)])

    assert best is not None
    assert best.profile.code == "BUC-SECOND"


def test_no_matching_equipment_returns_none_rather_than_raising():
    """An ordinary outcome the interface has to present, not an error condition."""
    assert best_profile(FrequencyRange(1_000, 2_000), [buc()]) is None


def test_every_rejection_carries_its_reason():
    """Section 13.5 makes profile selection operator-visible. "No equipment matched" without
    saying why is not something anyone can act on."""
    rf = FrequencyRange(31_000_000_000, 31_100_000_000)

    assert all(m.rejected_because for m in match_profiles(rf, [buc(), high_side()]))


# ---------------------------------------------------------------------------
# Validation findings
# ---------------------------------------------------------------------------
def test_an_unusable_profile_produces_a_blocking_finding():
    match = match_profiles(FrequencyRange(1_000, 2_000), [buc()])[0]

    findings = validation.check_conversion(match)

    assert [f.code for f in findings] == ["NO_EQUIPMENT_MATCH"]
    assert not validation.is_placeable(findings)


def test_an_inverting_profile_is_flagged_for_review():
    """So a spectrum plot is not read the wrong way round."""
    match = match_profiles(FrequencyRange(19_500_000_000, 19_800_000_000), [high_side()])[0]

    findings = validation.check_conversion(match)

    assert "EQUIPMENT_INVERTS" in {f.code for f in findings}
    assert validation.is_placeable(findings)


def test_an_if_outside_the_expected_band_warns_but_does_not_refuse():
    """The profile's own limits are the authority; the band is a review flag."""
    match = match_profiles(FrequencyRange(29_000_000_000, 29_500_000_000), [buc()])[0]

    findings = validation.check_conversion(match, band=FrequencyRange(1_000_000_000, 2_000_000_000))

    assert "IF_OUTSIDE_BAND" in {f.code for f in findings}
    assert validation.is_placeable(findings)


# ---------------------------------------------------------------------------
# The engine and the database must agree
# ---------------------------------------------------------------------------
def test_the_engine_and_the_database_agree_on_the_conversion_enumerations():
    from inventory.constants import ConversionMethod as DatabaseConversionMethod
    from inventory.constants import Sideband as DatabaseSideband

    assert {m.value for m in ConversionMethod} == set(DatabaseConversionMethod.values)
    assert {s.value for s in Sideband} == set(DatabaseSideband.values)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
#: Strategies that *construct* a valid (LO, RF) pair rather than generating both and
#: filtering. The two sidebands constrain the relationship in opposite directions — low-side
#: needs the LO below the RF, high-side needs it above — so an independent pair is almost
#: never valid for either, and filtering it produces a test that silently examines nothing.
LOS = st.integers(min_value=1_000_000_000, max_value=30_000_000_000)
WIDTHS = st.integers(min_value=1, max_value=500_000_000)
#: Distance from the LO to the near edge of the RF interval. Non-zero because an IF of
#: exactly 0 Hz would make the converted range start at zero, which is legal but not a
#: frequency anyone plans against.
SPACINGS = st.integers(min_value=1, max_value=2_000_000_000)


@st.composite
def low_side_pairs(draw) -> tuple[EquipmentProfileSpec, FrequencyRange]:
    """LO below the RF, as low-side injection requires."""
    lo = draw(LOS)
    spacing, width = draw(SPACINGS), draw(WIDTHS)
    rf = FrequencyRange(lo + spacing, lo + spacing + width)
    return buc(lo_hz=lo, rf_limits=FrequencyRange(1, rf.end_hz + 1)), rf


@st.composite
def high_side_pairs(draw) -> tuple[EquipmentProfileSpec, FrequencyRange]:
    """LO above the RF, as high-side injection requires."""
    lo = draw(LOS)
    spacing, width = draw(SPACINGS), draw(WIDTHS)
    # The whole interval must sit below the LO, so it is built downwards from it.
    end = lo - spacing
    assume(end - width > 0)
    rf = FrequencyRange(end - width, end)
    return high_side(lo_hz=lo, rf_limits=FrequencyRange(1, lo)), rf


@given(low_side_pairs())
def test_low_side_conversion_always_round_trips(pair):
    profile, rf = pair

    assert to_radio(to_intermediate(rf, profile), profile) == rf


@given(high_side_pairs())
def test_high_side_conversion_always_round_trips(pair):
    """The inverting case. Reflection is its own inverse, so this holds without a second
    constant — and it is the property that lets an IF plan be turned back into an RF plan."""
    profile, rf = pair

    assert to_radio(to_intermediate(rf, profile), profile) == rf


@given(high_side_pairs())
def test_an_inverting_conversion_always_preserves_width(pair):
    profile, rf = pair

    assert to_intermediate(rf, profile).width_hz == rf.width_hz


@given(low_side_pairs())
def test_a_non_inverting_conversion_always_preserves_width(pair):
    profile, rf = pair

    assert to_intermediate(rf, profile).width_hz == rf.width_hz


@given(high_side_pairs())
def test_high_side_conversion_always_reverses_the_edge_order(pair):
    """``IF = LO - RF``: the RF low edge becomes the IF high edge."""
    profile, rf = pair

    intermediate = to_intermediate(rf, profile)

    assert intermediate.start_hz == profile.lo_hz - rf.end_hz
    assert intermediate.end_hz == profile.lo_hz - rf.start_hz


@given(st.lists(st.sampled_from(["A", "B", "C", "D"]), min_size=1, max_size=4, unique=True))
def test_matching_is_always_deterministic(codes):
    """Whatever order the profiles arrive in, the result is the same."""
    rf = FrequencyRange(29_000_000_000, 29_500_000_000)
    profiles = [buc(f"BUC-{c}") for c in codes]

    forwards = [m.profile.code for m in match_profiles(rf, profiles)]
    backwards = [m.profile.code for m in match_profiles(rf, list(reversed(profiles)))]

    assert forwards == backwards == sorted(forwards)
