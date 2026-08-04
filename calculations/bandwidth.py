"""Bandwidth and edge arithmetic. Specification sections 9.2, 14.2, 14.3.

Every formula in the product lives here. §11 is explicit that the backend result is
authoritative and that no template or script recomputes it, and the way to keep that true
is to leave nowhere else for a formula to be written.

The relationships, from §9.2 and the Specification Dictionary's own calculation notes:

    occupied bandwidth  = symbol rate x (1 + roll-off)
    symbol rate         = occupied bandwidth / (1 + roll-off)
    occupied range      = centre +/- half the occupied bandwidth
    allocated range     = occupied range widened by the left and right guards

The rounding at each step is **A-09**, implemented once in :mod:`calculations.rounding`.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from calculations import rounding
from calculations.ranges import FrequencyRange
from calculations.translation import TranslationSpec, translate, untranslate
from calculations.types import BandwidthRequest, GuardWidths, Placement, TwoSidedPlacement


def occupied_bandwidth_hz(symbol_rate_sps: int, rolloff: Decimal) -> int:
    """Occupied bandwidth from symbol rate. Rounds **up** (**A-09**).

    Up, because the result states how much spectrum the transmission uses. Rounding down
    would claim it occupies less than it does, and the platform would allow a neighbour one
    Hz too close — the error would show up on air rather than in the tool.
    """
    _check_rolloff(rolloff)
    if symbol_rate_sps <= 0:
        raise ValueError(f"Symbol rate must be positive: {symbol_rate_sps}")
    # Exact: both operands have finite decimal expansions, so a lost digit here would be a
    # defect rather than a rounding decision. rounding.EXACT raises if that ever happens.
    exact = rounding.exact_product(symbol_rate_sps, Decimal(1) + rolloff)
    return rounding.ceil_hz(exact)


def symbol_rate_sps(occupied_bandwidth_hz: int, rolloff: Decimal) -> int:
    """Symbol rate from occupied bandwidth. Rounds **down** (**A-09**).

    Down, because the result states what the transmission can carry. The two directions
    round opposite ways on purpose: each errs towards the answer that cannot cause a
    collision or an over-claim.

    Not an exact inverse of :func:`occupied_bandwidth_hz`, and cannot be. The forward
    direction rounds up and this one rounds down, so a round trip returns a symbol rate at
    most one symbol/second below the original. ``tests/domain/test_bandwidth.py`` states
    that bound as a property rather than leaving it to be discovered.
    """
    _check_rolloff(rolloff)
    if occupied_bandwidth_hz <= 0:
        raise ValueError(f"Occupied bandwidth must be positive: {occupied_bandwidth_hz}")
    # Inherently inexact — 1/1.35 has no finite decimal form — so this division is carried
    # at full precision and the policy is applied in the next line, visibly.
    quotient = rounding.inexact_quotient(occupied_bandwidth_hz, Decimal(1) + rolloff)
    return rounding.floor_hz(quotient)


def resolve_request(request: BandwidthRequest) -> tuple[int, int]:
    """Complete a §9.2 request into ``(symbol_rate_sps, occupied_bandwidth_hz)``.

    The operator supplies one; this derives the other. The supplied value is returned
    unchanged — deriving and then re-deriving would round twice and drift.
    """
    if request.symbol_rate_sps is not None:
        return request.symbol_rate_sps, occupied_bandwidth_hz(
            request.symbol_rate_sps, request.rolloff
        )
    assert request.occupied_bandwidth_hz is not None  # guaranteed by BandwidthRequest
    return (
        symbol_rate_sps(request.occupied_bandwidth_hz, request.rolloff),
        request.occupied_bandwidth_hz,
    )


def occupied_range(centre_hz: int, bandwidth_hz: int) -> FrequencyRange:
    """The occupied interval, centred and symmetric.

    Built from a half-width rounded **up**, so an odd bandwidth produces a range one Hz
    wider than requested rather than one Hz narrower (**A-09**). The stored occupied
    bandwidth is therefore ``range.width_hz``, which is ``>=`` the computed value — never
    below it, which is the property every containment check downstream relies on.

    Symmetry is preserved exactly: the centre is recoverable from the range.
    """
    half = rounding.half_width_hz(bandwidth_hz)
    return FrequencyRange(centre_hz - half, centre_hz + half)


def allocated_range(occupied: FrequencyRange, guards: GuardWidths) -> FrequencyRange:
    """The interval actually reserved in the spectrum pool.

    This is what the exclusion constraint compares (§8.1, §8.3) — the occupied bandwidth
    plus both guards. Two placements whose *occupied* ranges are far apart still conflict
    if their allocated ranges touch, which is the whole point of a guard band.
    """
    return occupied.expanded(left_hz=guards.left_hz, right_hz=guards.right_hz)


def place(*, request: BandwidthRequest, centre_hz: int, guards: GuardWidths) -> Placement:
    """The whole calculation, start to finish. The engine's single entry point.

    Callers use this rather than the individual steps so that the order of operations —
    derive, centre, widen — is decided in one place. Assembling it by hand at three call
    sites is how two of them end up applying the guard before the rounding.
    """
    symbol_rate, bandwidth = resolve_request(request)
    occupied = occupied_range(centre_hz, bandwidth)
    return Placement(
        symbol_rate_sps=symbol_rate,
        rolloff=request.rolloff,
        # The range's own width, not the computed bandwidth: they differ by one Hz for an
        # odd bandwidth, and the range is what gets reserved, so the range is the truth.
        occupied_bandwidth_hz=occupied.width_hz,
        occupied=occupied,
        allocated=allocated_range(occupied, guards),
        guards=guards,
    )


def place_both_sides(
    *,
    request: BandwidthRequest,
    centre_hz: int,
    guards: GuardWidths,
    translation: TranslationSpec,
    entered_side: str = "UPLINK",
) -> TwoSidedPlacement:
    """Place a transmission on both legs of a payload. ADR-0006.

    One side is calculated and the other is its **image**, never a second independent
    calculation. §13.7's translation preserves width exactly, so deriving the far side by
    moving the interval whole keeps the two consistent to the Hz. Recomputing it from a
    translated centre would re-round the half-width and could differ by one Hz — which is
    enough to make an allocation that fits on one side fail containment on the other.

    ``entered_side`` says which leg ``centre_hz`` refers to. §9.3 lets the canonical entry
    side differ by direction (**OQ-28**), so both are supported and neither is assumed.
    """
    entered = place(request=request, centre_hz=centre_hz, guards=guards)

    if entered_side == "UPLINK":
        uplink, downlink = entered, _image_of(entered, translation, forward=True)
    else:
        downlink, uplink = entered, _image_of(entered, translation, forward=False)

    return TwoSidedPlacement(
        uplink=uplink,
        downlink=downlink,
        entered_side=entered_side,
        inverted=translation.inverts,
    )


def _image_of(source: Placement, translation: TranslationSpec, *, forward: bool) -> Placement:
    """The same transmission seen on the other leg.

    Everything except the two ranges is unchanged: the symbol rate, the roll-off, the
    occupied bandwidth and the guards describe the transmission, not the leg it is
    observed on. Only its position moves.
    """
    move = translate if forward else untranslate
    return dataclasses.replace(
        source,
        occupied=move(source.occupied, translation),
        allocated=move(source.allocated, translation),
    )


def _check_rolloff(rolloff: Decimal) -> None:
    """Reject a roll-off that is not a factor.

    A raised-cosine roll-off is a fraction of the symbol rate, so it belongs to ``[0, 1]``.
    A value of 35 is someone entering a percentage, and silently treating it as a factor of
    35 would produce a bandwidth thirty-six times too wide with no error anywhere. Which
    default applies per platform is **OQ-06**; the range is arithmetic, not policy.
    """
    if not isinstance(rolloff, Decimal):
        raise TypeError(
            f"Roll-off must be Decimal, not {type(rolloff).__name__}. A float roll-off "
            f"reintroduces binary floating point into the engineering path (section 14.1)."
        )
    if rolloff < 0 or rolloff > 1:
        raise ValueError(
            f"Roll-off must be a factor between 0 and 1, not {rolloff}. A percentage such "
            f"as 35 should be entered as 0.35."
        )
