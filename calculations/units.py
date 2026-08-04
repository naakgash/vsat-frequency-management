"""Frequency unit conversion, in one place.

Specification section 14.1 forbids binary floating point for engineering values, and
ADR-0003 stores every frequency as an integer number of Hz. People, however, work in MHz:
the forms accept MHz, the screens display MHz, and the database holds Hz.

That conversion therefore happens exactly twice in the product — on the way in and on the
way out — and both use this module. A second implementation somewhere else is how a
rounding rule drifts, and a frequency that is 1 Hz out is a frequency that fails an
exclusion constraint for reasons nobody can see.

It lives in ``calculations`` rather than in ``inventory`` because it is the lowest thing
in the engineering path: the forms, the display filters and the engine all need it, and
``calculations`` is the only module every one of them may import.

``specifications.templatetags.spec_tags`` performs its own conversion and is not a
duplicate of this one: it converts according to the unit an administrator recorded in the
Specification Dictionary, whereas this module converts a column that is Hz by construction.
``tests/domain/test_units.py`` compares the two so they cannot drift.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Exact by construction. ``Decimal`` rather than ``int`` so that dividing by it yields a
#: ``Decimal`` and never promotes the result to ``float``.
HZ_PER_MHZ = Decimal(1_000_000)


class SubHertzError(ValueError):
    """Raised when a value in MHz does not land on a whole number of Hz."""


def to_hz(megahertz: Decimal | int | str) -> int:
    """Convert MHz to whole Hz, refusing anything finer.

    Rounding here would be a quiet way to reintroduce the approximation ADR-0003 exists to
    prevent: the caller would get a frequency that is *nearly* the one they typed, and the
    difference would only surface as an inexplicable overlap much later.
    """
    try:
        hz = Decimal(megahertz) * HZ_PER_MHZ
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SubHertzError(f"{megahertz!r} is not a valid frequency in MHz.") from exc
    if hz != hz.to_integral_value():
        raise SubHertzError(f"{megahertz} MHz is finer than 1 Hz.")
    return int(hz)


def to_mhz(hertz: int | Decimal | None) -> Decimal | None:
    """Convert whole Hz to MHz, exactly. ``None`` passes through for optional columns."""
    if hertz is None:
        return None
    return Decimal(hertz) / HZ_PER_MHZ
