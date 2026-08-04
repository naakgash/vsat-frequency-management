"""Guard resolution. Specification sections 9.2, 13.6, 13.9 — ADR-0016.

A guard is not one number with one source. §13.6 gives a Frequency Window a default policy,
§13.9 gives a Satnet one, and §9.2 lets an operator *"select or accept"* a policy on the
path itself. Something has to decide which of those applies, and the answer must be the
same everywhere or two screens will show different allocated bandwidths for one placement.

The order, most specific first (ADR-0016)::

    1. an explicit per-path override entered by an authorised user
    2. the Satnet's default guard policy
    3. the canonical-side Frequency Window's default guard policy
    4. the system default guard policy

Both the resolved policy *and* the resolved Hz widths are stored on the Satnet Path when
one is created in S11, so editing a policy later cannot retroactively change an allocation
that was validated against the old widths — the same rule as ADR-0012, for the same reason.

**No guard values are supplied by this module or anywhere else.** Widths by band, window
and platform are **OQ-07**.
"""

from __future__ import annotations

from decimal import Decimal

from calculations import rounding
from calculations.types import GuardMode, GuardPolicySpec, GuardSource, GuardWidths

#: What applies when nothing is configured: no separation at all.
#:
#: Zero is the honest answer to "no policy exists", and it is deliberately not a plausible
#: default. Inventing 250 kHz here would be indistinguishable from a confirmed value once
#: it reached an allocation, which is exactly what §26.20 forbids. The validation layer
#: reports an unguarded placement so the absence is visible rather than assumed.
NO_GUARD = GuardWidths(left_hz=0, right_hz=0, source=GuardSource.NONE, policy_label="")


def resolve(policy: GuardPolicySpec | None, occupied_bandwidth_hz: int) -> GuardWidths:
    """Evaluate one policy against an occupied bandwidth.

    Widths round **up** (**A-09**): a guard is a minimum separation, so a fractional Hz
    must become a wider gap rather than a narrower one.
    """
    if policy is None:
        return NO_GUARD
    if occupied_bandwidth_hz <= 0:
        raise ValueError(f"Occupied bandwidth must be positive: {occupied_bandwidth_hz}")

    match policy.mode:
        case GuardMode.FIXED:
            left, right = policy.fixed_left_hz, policy.fixed_right_hz
        case GuardMode.PERCENT_OF_OCCUPIED:
            left = _percent_of(occupied_bandwidth_hz, policy.percent_left)
            right = _percent_of(occupied_bandwidth_hz, policy.percent_right)
        case GuardMode.MAX_OF_FIXED_AND_PERCENT:
            # "At least this many Hz, and at least this proportion" — the wider of the two
            # wins, which is what makes the mode meaningful for a band whose transmissions
            # span three orders of magnitude in bandwidth.
            left = max(
                policy.fixed_left_hz or 0,
                _percent_of(occupied_bandwidth_hz, policy.percent_left),
            )
            right = max(
                policy.fixed_right_hz or 0,
                _percent_of(occupied_bandwidth_hz, policy.percent_right),
            )
        case _:  # pragma: no cover - GuardPolicySpec rejects an unknown mode
            raise ValueError(f"Unknown guard mode: {policy.mode}")

    assert left is not None and right is not None  # guaranteed by GuardPolicySpec
    return GuardWidths(
        left_hz=left,
        right_hz=right,
        source=policy.source,
        policy_label=policy.label,
    )


def first_applicable(*candidates: GuardPolicySpec | None) -> GuardPolicySpec | None:
    """The most specific policy that exists, in the order given.

    Variadic rather than a list of four named arguments: the caller in S11 passes them in
    ADR-0016 order, and a signature naming all four levels would have to change every time
    a level is added. The order is the caller's to state and this function's to respect.
    """
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def resolve_hierarchy(
    *candidates: GuardPolicySpec | None, occupied_bandwidth_hz: int
) -> GuardWidths:
    """Pick the applicable policy and evaluate it, in one call.

    The combination callers actually want. Splitting it would let one of them resolve the
    hierarchy and evaluate a different policy.
    """
    return resolve(first_applicable(*candidates), occupied_bandwidth_hz)


def _percent_of(bandwidth_hz: int, percent: Decimal | None) -> int:
    """A percentage of the occupied bandwidth, rounded up to whole Hz.

    Exact multiplication, then one explicit ceiling. Percentages are stored as ``Decimal``
    precisely so this cannot go through binary floating point (§14.1): ``0.1`` as a float
    is not one tenth, and a guard derived from it would put an approximate edge on an
    interval the database compares exactly.
    """
    if percent is None:
        return 0
    hundredths = rounding.exact_product(bandwidth_hz, percent)
    return rounding.ceil_hz(rounding.inexact_quotient(hundredths, 100))
