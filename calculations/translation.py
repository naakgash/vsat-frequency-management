"""Payload translation: uplink RF to downlink RF, and back. Specification section 13.7.

§13.7 requires a **deterministic** mapping from one RF side to the other. That is why the
method and the constant are carried explicitly rather than inferred from the two windows:
any two windows sit at *some* offset from each other, and deriving the relationship from
their edges would produce a plausible number that is not the satellite's actual
translation.

Three methods, and one of them inverts::

    OFFSET_ADD        downlink = uplink + K          order preserved
    OFFSET_SUBTRACT   downlink = uplink - K          order preserved
    LO_REFLECT        downlink = K - uplink          order reversed

Applied to an interval rather than a frequency, the first two are a shift and the third is
a reflection — which is where the half-open convention earns its keep (**A-10**, ADR-0008).
`FrequencyRange.reflect` already handles the re-normalisation and is its own inverse; this
module is the thin layer that decides which of the two to apply.

**No translation constant is supplied here or anywhere else.** Which method and constant
applies to any real payload is **OQ-02**.
"""

from __future__ import annotations

import dataclasses
import enum

from calculations.ranges import FrequencyRange


class TranslationMethod(enum.StrEnum):
    """Mirrors ``inventory.constants.TranslationMethod``.

    Duplicated because ``calculations`` sits below ``inventory`` and cannot import it.
    ``tests/domain/test_translation.py`` compares the two member-for-member: a method added
    to the database and not here would fall through :func:`translate`'s match and raise,
    which is noisy but safe — the dangerous version of that mistake is the one in
    ``guards``, where the fallthrough would silently mean *no guard*.
    """

    OFFSET_ADD = "OFFSET_ADD"
    OFFSET_SUBTRACT = "OFFSET_SUBTRACT"
    LO_REFLECT = "LO_REFLECT"


@dataclasses.dataclass(frozen=True)
class TranslationSpec:
    """A Payload Path reduced to the arithmetic the engine needs.

    Built from an ``inventory.PayloadPath`` row by the caller; the engine never sees the
    model. ``spectral_inversion`` is carried because the *model* stores it independently of
    the method — see :func:`inverts` for what that means and why the two are not merged.
    """

    method: TranslationMethod
    constant_hz: int
    spectral_inversion: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        if self.constant_hz < 0:
            raise ValueError(
                f"A translation constant is a magnitude, not a direction: {self.constant_hz}. "
                f"Use OFFSET_SUBTRACT rather than a negative OFFSET_ADD."
            )

    @property
    def inverts(self) -> bool:
        """Does this path invert the spectrum?

        ``LO_REFLECT`` inverts by construction. The stored flag is *also* consulted because
        the model keeps it separately: a path may be recorded as inverting for a reason the
        translation method does not express.

        The two are reported together and computed apart. :func:`translate` follows the
        **method**, because that is the only thing that says *how much*. A flag set on an
        offset method is a contradiction the engine cannot resolve — there is no reflection
        constant to reflect through — so it is reported by
        :func:`calculations.validation.check_translation` rather than guessed at.
        """
        return self.method is TranslationMethod.LO_REFLECT or self.spectral_inversion

    @property
    def is_contradictory(self) -> bool:
        """True when the stored inversion flag disagrees with the method's arithmetic."""
        return self.spectral_inversion and self.method is not TranslationMethod.LO_REFLECT


def translate(uplink: FrequencyRange, spec: TranslationSpec) -> FrequencyRange:
    """Map an uplink interval to its downlink interval.

    Width is preserved by all three methods. Under ``LO_REFLECT`` the interval is reflected
    rather than shifted, so the frequency that was at the bottom edge ends up at the top —
    and one Hz of *edge*, not of width, moves in the re-normalisation (**A-10**).
    """
    match spec.method:
        case TranslationMethod.OFFSET_ADD:
            return uplink.shift(spec.constant_hz)
        case TranslationMethod.OFFSET_SUBTRACT:
            return uplink.shift(-spec.constant_hz)
        case TranslationMethod.LO_REFLECT:
            return uplink.reflect(spec.constant_hz)
        case _:  # pragma: no cover - TranslationMethod is closed
            raise ValueError(f"Unknown translation method: {spec.method}")


def untranslate(downlink: FrequencyRange, spec: TranslationSpec) -> FrequencyRange:
    """Map a downlink interval back to its uplink interval. The exact inverse.

    Needed because an operator may work from either side: §9.3 lets the canonical entry side
    differ per direction (**OQ-28**), so the platform has to be able to start from the
    downlink and derive the uplink just as readily as the other way round.

    ``translate`` followed by ``untranslate`` returns the original interval exactly — for
    every method, including the inverting one. That is a property test rather than a
    comment, because it is the single claim the two-sided reservation in ADR-0006 rests on.
    """
    match spec.method:
        case TranslationMethod.OFFSET_ADD:
            return downlink.shift(-spec.constant_hz)
        case TranslationMethod.OFFSET_SUBTRACT:
            return downlink.shift(spec.constant_hz)
        case TranslationMethod.LO_REFLECT:
            # Reflection is its own inverse; there is no second constant to get wrong.
            return downlink.reflect(spec.constant_hz)
        case _:  # pragma: no cover
            raise ValueError(f"Unknown translation method: {spec.method}")


def translate_frequency(uplink_hz: int, spec: TranslationSpec) -> int:
    """Translate a single frequency — a centre, typically, for display.

    Not used to derive edges. Translating a centre and rebuilding a range around it would
    reintroduce the rounding that :func:`translate` avoids by moving the interval whole.
    """
    match spec.method:
        case TranslationMethod.OFFSET_ADD:
            return uplink_hz + spec.constant_hz
        case TranslationMethod.OFFSET_SUBTRACT:
            return uplink_hz - spec.constant_hz
        case TranslationMethod.LO_REFLECT:
            return spec.constant_hz - uplink_hz
        case _:  # pragma: no cover
            raise ValueError(f"Unknown translation method: {spec.method}")
