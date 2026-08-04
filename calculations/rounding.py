"""The one rounding policy. Specification section 14.3, design assumption **A-09**.

§14.3 requires *"one documented rounding policy"* and does not state what it should be.
**A-09** adopts **outward** rounding: occupied bandwidth rounds up, derived symbol rate
rounds down. The platform therefore never under-states occupancy and never over-states
capability, so an error of ≤1 Hz always costs spectrum rather than causing a collision.

This module exists so that policy has exactly one implementation. Every ceiling and every
floor in the engineering path comes from here.

Two kinds of rounding
---------------------

The distinction this module draws is the reason it exists at all.

**Policy rounding** is deliberate: the answer genuinely is not an integer number of Hz and
A-09 says which way to go. :func:`ceil_hz` and :func:`floor_hz` are that, and each call is
a visible decision.

**Accidental rounding** is a defect: a multiplication that silently loses digits because a
``Decimal`` context ran out of precision. It produces a number that is nearly right, which
is the worst kind. :data:`EXACT` traps ``Inexact``, so arithmetic that must be exact
*raises* instead of quietly approximating.

Keeping the two apart means a stray digit cannot masquerade as the rounding policy.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, Inexact, InvalidOperation

#: Precision for arithmetic that must not lose a digit.
#:
#: Generous by design. The widest realistic product is a symbol rate near 10⁹ times a
#: roll-off factor carrying a handful of decimal places — under twenty significant digits.
#: Fifty leaves no room for an argument about whether the limit was reached.
EXACT_PRECISION = 50

#: Context for arithmetic that must be exact. ``Inexact`` is trapped, so a multiplication
#: that would silently drop digits raises instead. Nothing in the engineering path is
#: allowed to approximate without saying so.
EXACT = Context(
    prec=EXACT_PRECISION,
    traps=[Inexact, InvalidOperation],
)

#: Context for arithmetic that is *inherently* inexact — division, chiefly. A quotient like
#: ``occupied / 1.35`` has no exact decimal form, so trapping ``Inexact`` here would reject
#: a correct calculation. The result is carried at full precision and then handed to
#: :func:`floor_hz` or :func:`ceil_hz`, which applies the policy in one visible step.
INEXACT = Context(prec=EXACT_PRECISION, traps=[InvalidOperation])


class ExactnessError(ArithmeticError):
    """Raised when arithmetic that had to be exact was not.

    Not expected in practice. It is here because the alternative to raising is returning a
    frequency that is *almost* right, and an almost-right edge is what makes an exclusion
    constraint reject two intervals that ought to be adjacent.
    """


def exact_product(left: Decimal | int, right: Decimal | int) -> Decimal:
    """Multiply exactly, or raise.

    Used where the inputs have finite decimal expansions and the product must too: symbol
    rate times ``1 + roll-off``, a percentage of a bandwidth.
    """
    try:
        return EXACT.multiply(Decimal(left), Decimal(right))
    except Inexact as exc:  # pragma: no cover - needs a 50-digit operand to reach
        raise ExactnessError(
            f"{left} x {right} cannot be represented exactly at {EXACT_PRECISION} "
            f"significant digits. Refusing to return an approximate frequency."
        ) from exc


def inexact_quotient(numerator: Decimal | int, denominator: Decimal | int) -> Decimal:
    """Divide at full precision, without applying any policy.

    The caller decides which way the result rounds. Returning a ``Decimal`` rather than an
    ``int`` is deliberate: it forces that decision to be made explicitly, at the call site,
    by naming :func:`ceil_hz` or :func:`floor_hz`.
    """
    return INEXACT.divide(Decimal(numerator), Decimal(denominator))


def ceil_hz(value: Decimal | int) -> int:
    """Round up to a whole number of Hz. **A-09** applies this to occupied bandwidth.

    Rounding up never under-states how much spectrum a transmission occupies, so the error
    is always in the direction of reserving slightly too much.
    """
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def floor_hz(value: Decimal | int) -> int:
    """Round down to a whole number of Hz. **A-09** applies this to a derived symbol rate.

    Rounding down never over-states what the transmission can carry. The two directions are
    opposites on purpose: each errs towards the answer that cannot cause a collision.
    """
    return int(Decimal(value).to_integral_value(rounding=ROUND_FLOOR))


def half_width_hz(bandwidth_hz: int) -> int:
    """Half a bandwidth, rounded up (**A-09**).

    An odd bandwidth has no exact half, so the placement is symmetric around the centre and
    one Hz wider than requested rather than one Hz narrower. The occupied range built from
    it is therefore always at least the computed bandwidth — never less, which is the
    property the reservation depends on.
    """
    if bandwidth_hz < 0:
        raise ValueError(f"A bandwidth cannot be negative: {bandwidth_hz}")
    return -(-bandwidth_hz // 2)  # integer ceiling division; no Decimal needed
