"""RF ↔ IF conversion and equipment matching. Specification section 13.5.

An Equipment Profile converts between the radio frequency on the satellite side and the
intermediate frequency the modem works in. §13.5 requires ``RF = LO + IF``, ``RF = LO - IF``,
``IF = |RF - LO|`` and a fixed translation offset.

That third form is the reason ``sideband`` exists as its own field. ``IF = |RF - LO|`` is
**not invertible on its own** — an IF of 1 GHz with an LO of 28 GHz could mean an RF of 27
or 29 GHz, and nothing in the absolute value says which. ``sideband`` records which side of
the RF the local oscillator sits on, and that is what makes the conversion a function in
both directions:

| Method | Sideband | Up (IF→RF) | Down (RF→IF) | Inverts |
|---|---|---|---|---|
| ``LO_PLUS_IF`` | ``LOW_SIDE`` (LO < RF) | ``RF = LO + IF`` | ``IF = RF - LO`` | no |
| ``LO_MINUS_IF`` | ``HIGH_SIDE`` (LO > RF) | ``RF = LO - IF`` | ``IF = LO - RF`` | **yes** |
| ``FIXED_OFFSET`` | either | ``RF = IF + offset`` | ``IF = RF - offset`` | no |

High-side injection reverses the spectrum, so its interval mapping is a reflection through
the LO rather than a shift — the same arithmetic as an inverting payload translation, and
the same 1 Hz of edge movement (**A-10**, ADR-0008).

**No profile values are supplied.** RF, IF and LO limits per site and model are **OQ-04**,
and remote-side equipment is out of scope pending **OQ-26**.
"""

from __future__ import annotations

import dataclasses
import enum

from calculations.ranges import FrequencyRange


class ConversionMethod(enum.StrEnum):
    """Mirrors ``inventory.constants.ConversionMethod``."""

    LO_PLUS_IF = "LO_PLUS_IF"
    LO_MINUS_IF = "LO_MINUS_IF"
    FIXED_OFFSET = "FIXED_OFFSET"


class Sideband(enum.StrEnum):
    """Which side of the RF the local oscillator sits on.

    Mirrors ``inventory.constants.Sideband``. Low-side injection is non-inverting; high-side
    injection inverts the spectrum.
    """

    LOW_SIDE = "LOW_SIDE"
    HIGH_SIDE = "HIGH_SIDE"


#: Which sideband each method implies. The database enforces the same pairing in
#: ``ck_equipment_conversion_sideband``; it is repeated here because the engine is
#: reachable from the importer, which never sees that constraint until commit time.
REQUIRED_SIDEBAND: dict[ConversionMethod, Sideband | None] = {
    ConversionMethod.LO_PLUS_IF: Sideband.LOW_SIDE,
    ConversionMethod.LO_MINUS_IF: Sideband.HIGH_SIDE,
    # A fixed offset says nothing about where the LO sits, so either is acceptable.
    ConversionMethod.FIXED_OFFSET: None,
}


class ConversionError(ValueError):
    """Raised when a conversion cannot produce a usable interval."""


@dataclasses.dataclass(frozen=True)
class EquipmentProfileSpec:
    """An Equipment Profile reduced to what the engine needs.

    ``offset_hz`` defaults to ``lo_hz``. The model carries no separate offset column — for
    ``FIXED_OFFSET`` the LO field *is* the offset, which is how §13.5's fourth form is
    represented without adding a column that is null for every other method. Naming it
    separately here keeps the arithmetic readable rather than having ``FIXED_OFFSET``
    quietly reuse a field called ``lo``.
    """

    code: str
    method: ConversionMethod
    sideband: Sideband
    lo_hz: int
    rf_limits: FrequencyRange
    if_limits: FrequencyRange
    priority: int = 100
    spectral_inversion: bool = False
    label: str = ""
    offset_hz: int | None = None

    def __post_init__(self) -> None:
        required = REQUIRED_SIDEBAND[self.method]
        if required is not None and self.sideband is not required:
            raise ValueError(
                f"{self.method} implies {required}, not {self.sideband}. A profile claiming "
                f"low-side injection while subtracting the IF from the LO would silently "
                f"produce a wrong IF (section 13.5)."
            )
        if self.lo_hz <= 0:
            raise ValueError(f"The local oscillator frequency must be positive: {self.lo_hz}")

    @property
    def translation_hz(self) -> int:
        """The constant this profile converts through: the offset, or the LO."""
        return self.offset_hz if self.offset_hz is not None else self.lo_hz

    @property
    def inverts(self) -> bool:
        """Does this profile invert the spectrum?

        High-side injection does, by construction. The stored flag is combined rather than
        derived so a profile that inverts for another reason can say so — the same
        arrangement as a Payload Path's.
        """
        return self.method is ConversionMethod.LO_MINUS_IF or self.spectral_inversion


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def to_intermediate(rf: FrequencyRange, profile: EquipmentProfileSpec) -> FrequencyRange:
    """Down-convert an RF interval to IF.

    Width is preserved. Under ``LO_MINUS_IF`` the interval is reflected through the LO, so
    the RF bottom edge becomes the IF top edge.
    """
    match profile.method:
        case ConversionMethod.LO_PLUS_IF:
            converted = rf.shift(-profile.lo_hz)
        case ConversionMethod.LO_MINUS_IF:
            converted = _reflect_checked(rf, profile.lo_hz, profile)
        case ConversionMethod.FIXED_OFFSET:
            converted = rf.shift(-profile.translation_hz)
        case _:  # pragma: no cover - ConversionMethod is closed
            raise ConversionError(f"Unknown conversion method: {profile.method}")
    _check_non_negative(converted, profile, "IF")
    return converted


def to_radio(intermediate: FrequencyRange, profile: EquipmentProfileSpec) -> FrequencyRange:
    """Up-convert an IF interval to RF. The exact inverse of :func:`to_intermediate`."""
    match profile.method:
        case ConversionMethod.LO_PLUS_IF:
            converted = intermediate.shift(profile.lo_hz)
        case ConversionMethod.LO_MINUS_IF:
            # Reflection is its own inverse: RF = LO - IF and IF = LO - RF are one map.
            converted = _reflect_checked(intermediate, profile.lo_hz, profile)
        case ConversionMethod.FIXED_OFFSET:
            converted = intermediate.shift(profile.translation_hz)
        case _:  # pragma: no cover
            raise ConversionError(f"Unknown conversion method: {profile.method}")
    _check_non_negative(converted, profile, "RF")
    return converted


def _reflect_checked(
    source: FrequencyRange, constant_hz: int, profile: EquipmentProfileSpec
) -> FrequencyRange:
    """Reflect, refusing the case that would produce a negative frequency.

    ``LO - x`` is only meaningful while ``x < LO``. High-side injection means the LO is
    above the RF, so a source interval reaching past the LO is a profile applied to
    spectrum it cannot serve — worth a clear message rather than a negative frequency that
    fails a range constructor three calls later.
    """
    if source.end_hz > constant_hz:
        raise ConversionError(
            f"Profile {profile.code} reflects through {constant_hz} Hz, but {source} reaches "
            f"above it. High-side injection requires the local oscillator to sit above the "
            f"whole interval."
        )
    return source.reflect(constant_hz)


def _check_non_negative(
    converted: FrequencyRange, profile: EquipmentProfileSpec, side: str
) -> None:
    if converted.start_hz < 0:
        raise ConversionError(
            f"Profile {profile.code} converts to {converted}, which is below 0 Hz. "
            f"The {side} interval is outside anything this profile can produce."
        )


# ---------------------------------------------------------------------------
# Matching — specification section 13.5
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class ProfileMatch:
    """One profile considered against an RF interval, and what came of it."""

    profile: EquipmentProfileSpec
    intermediate: FrequencyRange | None
    #: Empty when the profile is usable. Populated with a human-readable reason otherwise —
    #: a matcher that only returns the winners cannot explain why the obvious candidate
    #: was not one.
    rejected_because: str = ""

    @property
    def is_usable(self) -> bool:
        return not self.rejected_because


def evaluate(rf: FrequencyRange, profile: EquipmentProfileSpec) -> ProfileMatch:
    """Can this profile carry this RF interval, and at what IF?

    Two conditions, in the order a person would check them: the RF has to be inside the
    profile's RF limits, and the IF it converts to has to be inside its IF limits. Both are
    containment of the whole interval, not of its centre — a transmission whose edge falls
    outside the profile's range is not carried by it.
    """
    if not profile.rf_limits.contains(rf):
        return ProfileMatch(
            profile=profile,
            intermediate=None,
            rejected_because=f"RF {rf} is outside the profile's RF limits {profile.rf_limits}",
        )

    try:
        intermediate = to_intermediate(rf, profile)
    except ConversionError as exc:
        return ProfileMatch(profile=profile, intermediate=None, rejected_because=str(exc))

    if not profile.if_limits.contains(intermediate):
        return ProfileMatch(
            profile=profile,
            intermediate=intermediate,
            rejected_because=(
                f"IF {intermediate} is outside the profile's IF limits {profile.if_limits}"
            ),
        )

    return ProfileMatch(profile=profile, intermediate=intermediate)


def match_profiles(rf: FrequencyRange, profiles: list[EquipmentProfileSpec]) -> list[ProfileMatch]:
    """Every profile evaluated against an RF interval, usable ones first.

    Usable matches are ordered by ``priority`` and then by ``code``. The tie-break on code
    is what makes the result **deterministic**: two profiles at priority 100 must not swap
    places between two runs, or the same request would select different equipment on
    Tuesday than on Monday and nothing would explain it.

    Rejected profiles are returned too, each carrying its reason. §13.5 makes profile
    selection an operator-visible decision, and "no equipment matched" without saying why is
    not a decision anyone can act on.

    Selection is by RF/IF containment and priority — **never** by parsing the label.
    ``LOW``, ``MID`` and ``HIGH`` are free text (§13.5) and drive no branching here.
    """
    evaluated = [evaluate(rf, profile) for profile in profiles]
    usable = [m for m in evaluated if m.is_usable]
    rejected = [m for m in evaluated if not m.is_usable]
    usable.sort(key=lambda m: (m.profile.priority, m.profile.code))
    rejected.sort(key=lambda m: (m.profile.priority, m.profile.code))
    return usable + rejected


def best_profile(rf: FrequencyRange, profiles: list[EquipmentProfileSpec]) -> ProfileMatch | None:
    """The highest-priority usable profile, or ``None``.

    ``None`` rather than an exception: no matching equipment is an ordinary outcome the
    interface has to present alongside the reasons, not an error condition.
    """
    for match in match_profiles(rf, profiles):
        if match.is_usable:
            return match
    return None
