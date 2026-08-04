"""Guard resolution. Sections 9.2, 13.6, 13.9 — ADR-0016.

Two things are being protected here. One is the arithmetic. The other is the *hierarchy*:
if two screens disagree about which policy applies, they will show two different allocated
bandwidths for one placement and neither will look wrong on its own.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from calculations import guards
from calculations.types import GuardMode, GuardPolicySpec, GuardSource


def fixed(left: int, right: int, source: GuardSource = GuardSource.SYSTEM) -> GuardPolicySpec:
    return GuardPolicySpec(
        mode=GuardMode.FIXED, source=source, fixed_left_hz=left, fixed_right_hz=right
    )


def percent(left: str, right: str, source: GuardSource = GuardSource.SYSTEM) -> GuardPolicySpec:
    return GuardPolicySpec(
        mode=GuardMode.PERCENT_OF_OCCUPIED,
        source=source,
        percent_left=Decimal(left),
        percent_right=Decimal(right),
    )


# ---------------------------------------------------------------------------
# The modes
# ---------------------------------------------------------------------------
def test_a_fixed_policy_returns_its_own_widths():
    widths = guards.resolve(fixed(500_000, 250_000), occupied_bandwidth_hz=10_000_000)

    assert (widths.left_hz, widths.right_hz) == (500_000, 250_000)


def test_a_percentage_policy_scales_with_the_occupied_bandwidth():
    widths = guards.resolve(percent("5", "5"), occupied_bandwidth_hz=10_000_000)

    assert (widths.left_hz, widths.right_hz) == (500_000, 500_000)


def test_a_percentage_guard_rounds_up():
    """**A-09**. A guard is a *minimum* separation, so a fractional Hz has to become a
    wider gap rather than a narrower one."""
    # 5% of 101 Hz is 5.05 Hz.
    widths = guards.resolve(percent("5", "5"), occupied_bandwidth_hz=101)

    assert widths.left_hz == 6


def test_the_combined_mode_takes_whichever_is_wider():
    policy = GuardPolicySpec(
        mode=GuardMode.MAX_OF_FIXED_AND_PERCENT,
        source=GuardSource.SYSTEM,
        fixed_left_hz=1_000_000,
        fixed_right_hz=1_000_000,
        percent_left=Decimal("5"),
        percent_right=Decimal("5"),
    )

    # A narrow transmission: the fixed floor wins.
    narrow = guards.resolve(policy, occupied_bandwidth_hz=2_000_000)
    # A wide one: 5% exceeds the floor.
    wide = guards.resolve(policy, occupied_bandwidth_hz=100_000_000)

    assert narrow.left_hz == 1_000_000
    assert wide.left_hz == 5_000_000


def test_no_policy_means_no_guard():
    """Zero is the honest answer to "nothing is configured".

    Inventing a plausible 250 kHz here would be indistinguishable from a confirmed value
    once it reached an allocation, which is what section 26.20 forbids. The validation
    layer reports the absence instead.
    """
    widths = guards.resolve(None, occupied_bandwidth_hz=10_000_000)

    assert widths == guards.NO_GUARD
    assert widths.total_hz == 0


def test_a_policy_missing_its_own_values_is_refused():
    """The same rule as ck_guard_mode_has_required_values, checked here too because the
    engine is reachable from the importer, which never goes through a form."""
    with pytest.raises(ValueError, match="fixed_left_hz"):
        GuardPolicySpec(mode=GuardMode.FIXED, source=GuardSource.SYSTEM)


def test_a_negative_guard_value_is_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        GuardPolicySpec(
            mode=GuardMode.FIXED,
            source=GuardSource.SYSTEM,
            fixed_left_hz=-1,
            fixed_right_hz=0,
        )


# ---------------------------------------------------------------------------
# The hierarchy — ADR-0016
# ---------------------------------------------------------------------------
def test_an_explicit_override_beats_every_default():
    resolved = guards.resolve_hierarchy(
        fixed(1, 1, GuardSource.OVERRIDE),
        fixed(2, 2, GuardSource.SATNET),
        fixed(3, 3, GuardSource.WINDOW),
        fixed(4, 4, GuardSource.SYSTEM),
        occupied_bandwidth_hz=1_000_000,
    )

    assert resolved.source is GuardSource.OVERRIDE
    assert resolved.left_hz == 1


def test_the_satnet_default_beats_the_window_and_system_defaults():
    resolved = guards.resolve_hierarchy(
        None,
        fixed(2, 2, GuardSource.SATNET),
        fixed(3, 3, GuardSource.WINDOW),
        fixed(4, 4, GuardSource.SYSTEM),
        occupied_bandwidth_hz=1_000_000,
    )

    assert resolved.source is GuardSource.SATNET


def test_the_window_default_beats_the_system_default():
    resolved = guards.resolve_hierarchy(
        None,
        None,
        fixed(3, 3, GuardSource.WINDOW),
        fixed(4, 4, GuardSource.SYSTEM),
        occupied_bandwidth_hz=1_000_000,
    )

    assert resolved.source is GuardSource.WINDOW


def test_the_system_default_applies_when_nothing_else_is_set():
    resolved = guards.resolve_hierarchy(
        None, None, None, fixed(4, 4, GuardSource.SYSTEM), occupied_bandwidth_hz=1_000_000
    )

    assert resolved.source is GuardSource.SYSTEM


def test_an_empty_hierarchy_resolves_to_no_guard():
    resolved = guards.resolve_hierarchy(None, None, None, None, occupied_bandwidth_hz=1_000_000)

    assert resolved.source is GuardSource.NONE
    assert resolved.total_hz == 0


def test_the_resolved_source_is_reported():
    """Section 9.2 lets an operator "select or accept" a policy. Accepting one whose origin
    you cannot see is not a decision, and a reviewer needs to tell an explicit override from
    an inherited default."""
    resolved = guards.resolve(
        GuardPolicySpec(
            mode=GuardMode.FIXED,
            source=GuardSource.WINDOW,
            label="KA-DEFAULT",
            fixed_left_hz=100,
            fixed_right_hz=100,
        ),
        occupied_bandwidth_hz=1_000_000,
    )

    assert resolved.source is GuardSource.WINDOW
    assert resolved.policy_label == "KA-DEFAULT"


# ---------------------------------------------------------------------------
# The engine's enumerations must match the database's
# ---------------------------------------------------------------------------
def test_the_engine_and_the_database_agree_on_the_guard_modes():
    """``calculations`` cannot import ``inventory`` — the dependency runs the other way —
    so ``GuardMode`` is mirrored rather than shared.

    Mirroring is cheap; drifting is not. A mode added to the database and not here would
    fall through :func:`guards.resolve`'s match and resolve to no guard at all, on a
    policy that looks correctly configured.
    """
    from inventory.constants import GuardMode as DatabaseGuardMode

    assert {m.value for m in GuardMode} == set(DatabaseGuardMode.values)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
BANDWIDTHS = st.integers(min_value=1, max_value=500_000_000)
PERCENTS = st.decimals(min_value=Decimal(0), max_value=Decimal(50), places=3)


@given(BANDWIDTHS, PERCENTS, PERCENTS)
def test_a_percentage_guard_is_never_below_the_exact_proportion(occupied, left, right):
    widths = guards.resolve(percent(str(left), str(right)), occupied_bandwidth_hz=occupied)

    assert widths.left_hz >= occupied * left / 100
    assert widths.right_hz >= occupied * right / 100


@given(BANDWIDTHS, PERCENTS)
def test_a_percentage_guard_is_monotonic_in_the_bandwidth(occupied, pct):
    """A wider transmission never gets a narrower proportional guard."""
    narrow = guards.resolve(percent(str(pct), str(pct)), occupied_bandwidth_hz=occupied)
    wide = guards.resolve(percent(str(pct), str(pct)), occupied_bandwidth_hz=occupied * 2)

    assert wide.left_hz >= narrow.left_hz


@given(BANDWIDTHS, st.integers(min_value=0, max_value=10**9), PERCENTS)
def test_the_combined_mode_is_never_below_either_component(occupied, fixed_hz, pct):
    policy = GuardPolicySpec(
        mode=GuardMode.MAX_OF_FIXED_AND_PERCENT,
        source=GuardSource.SYSTEM,
        fixed_left_hz=fixed_hz,
        fixed_right_hz=fixed_hz,
        percent_left=pct,
        percent_right=pct,
    )

    combined = guards.resolve(policy, occupied_bandwidth_hz=occupied)
    only_fixed = guards.resolve(fixed(fixed_hz, fixed_hz), occupied_bandwidth_hz=occupied)
    only_percent = guards.resolve(percent(str(pct), str(pct)), occupied_bandwidth_hz=occupied)

    assert combined.left_hz >= only_fixed.left_hz
    assert combined.left_hz >= only_percent.left_hz


@given(BANDWIDTHS)
def test_guards_are_always_whole_hertz(occupied):
    widths = guards.resolve(percent("3.333", "7.777"), occupied_bandwidth_hz=occupied)

    assert isinstance(widths.left_hz, int)
    assert isinstance(widths.right_hz, int)
