"""Payload translation. Section 13.7, assumption **A-10**.

The round-trip property is the load-bearing one. ADR-0006 reserves both legs of a Satnet
Path by deriving one from the other, and that is only sound if the derivation is exactly
reversible — including through an inverting path, which is the case where it is least
obvious.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from calculations import validation
from calculations.ranges import FrequencyRange
from calculations.translation import (
    TranslationMethod,
    TranslationSpec,
    translate,
    translate_frequency,
    untranslate,
)

UPLINK = FrequencyRange(29_000_000_000, 29_500_000_000)


def spec(method: TranslationMethod, constant: int, **extra) -> TranslationSpec:
    return TranslationSpec(method=method, constant_hz=constant, **extra)


@st.composite
def ranges(draw) -> FrequencyRange:
    start = draw(st.integers(min_value=0, max_value=30_000_000_000))
    width = draw(st.integers(min_value=1, max_value=2_000_000_000))
    return FrequencyRange(start, start + width)


# ---------------------------------------------------------------------------
# The three methods
# ---------------------------------------------------------------------------
def test_an_added_offset_shifts_the_interval_up():
    result = translate(UPLINK, spec(TranslationMethod.OFFSET_ADD, 1_000_000_000))

    assert result == FrequencyRange(30_000_000_000, 30_500_000_000)


def test_a_subtracted_offset_shifts_the_interval_down():
    """The common Ka-band case: a 30 GHz uplink translated to a 20 GHz downlink."""
    result = translate(UPLINK, spec(TranslationMethod.OFFSET_SUBTRACT, 10_000_000_000))

    assert result == FrequencyRange(19_000_000_000, 19_500_000_000)


def test_a_reflection_reverses_the_interval():
    """**A-10**. ``downlink = K - uplink``, so the uplink's low edge becomes the downlink's
    high edge. Width is preserved exactly."""
    result = translate(UPLINK, spec(TranslationMethod.LO_REFLECT, 60_000_000_000))

    assert result == FrequencyRange(30_500_000_000, 31_000_000_000)
    assert result.width_hz == UPLINK.width_hz


def test_every_method_preserves_width():
    for method, constant in (
        (TranslationMethod.OFFSET_ADD, 1_000_000_000),
        (TranslationMethod.OFFSET_SUBTRACT, 10_000_000_000),
        (TranslationMethod.LO_REFLECT, 60_000_000_000),
    ):
        assert translate(UPLINK, spec(method, constant)).width_hz == UPLINK.width_hz


def test_a_negative_constant_is_refused():
    """A translation constant is a magnitude; the direction is the method's job. Allowing
    both would give two ways to express one translation and let them disagree."""
    with pytest.raises(ValueError, match="magnitude, not a direction"):
        TranslationSpec(method=TranslationMethod.OFFSET_ADD, constant_hz=-1)


# ---------------------------------------------------------------------------
# Reversibility — the claim ADR-0006 rests on
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "constant"),
    [
        (TranslationMethod.OFFSET_ADD, 1_000_000_000),
        (TranslationMethod.OFFSET_SUBTRACT, 10_000_000_000),
        (TranslationMethod.LO_REFLECT, 60_000_000_000),
    ],
)
def test_translating_and_untranslating_returns_the_original(method, constant):
    translation = spec(method, constant)

    assert untranslate(translate(UPLINK, translation), translation) == UPLINK


def test_an_operator_may_start_from_either_side():
    """Section 9.3 lets the canonical entry side differ by direction (OQ-28), so the
    platform has to derive the uplink from the downlink as readily as the reverse."""
    translation = spec(TranslationMethod.OFFSET_SUBTRACT, 10_000_000_000)
    downlink = FrequencyRange(19_000_000_000, 19_500_000_000)

    assert untranslate(downlink, translation) == UPLINK


# ---------------------------------------------------------------------------
# The inversion flag
# ---------------------------------------------------------------------------
def test_a_reflecting_path_reports_that_it_inverts():
    assert spec(TranslationMethod.LO_REFLECT, 60_000_000_000).inverts


def test_an_offset_path_does_not_invert():
    assert not spec(TranslationMethod.OFFSET_ADD, 1_000_000_000).inverts


def test_the_stored_flag_can_also_mark_a_path_as_inverting():
    """The model keeps the flag independently of the method, so a path may be recorded as
    inverting for a reason the method does not express."""
    assert spec(TranslationMethod.OFFSET_ADD, 1_000_000_000, spectral_inversion=True).inverts


def test_an_offset_path_flagged_as_inverting_is_reported_as_contradictory():
    """It is not resolvable, so it is reported rather than guessed at.

    An inverting path needs a reflection constant, and an offset does not carry one — there
    is no arithmetic that could honour the flag. Picking either interpretation would produce
    a downlink that looks plausible and is wrong.
    """
    contradictory = spec(TranslationMethod.OFFSET_ADD, 1_000_000_000, spectral_inversion=True)

    findings = validation.check_translation(contradictory)

    assert [f.code for f in findings] == ["INVERSION_WITHOUT_REFLECTION"]
    assert not validation.is_placeable(findings)


def test_a_consistent_path_reports_nothing():
    assert validation.check_translation(spec(TranslationMethod.LO_REFLECT, 60_000_000_000)) == []
    assert validation.check_translation(spec(TranslationMethod.OFFSET_ADD, 1)) == []


# ---------------------------------------------------------------------------
# Single frequencies
# ---------------------------------------------------------------------------
def test_a_single_frequency_translates_by_the_same_rule():
    assert (
        translate_frequency(29_145_000_000, spec(TranslationMethod.OFFSET_SUBTRACT, 10_000_000_000))
        == 19_145_000_000
    )


def test_a_reflected_frequency_mirrors_through_the_constant():
    assert (
        translate_frequency(29_145_000_000, spec(TranslationMethod.LO_REFLECT, 60_000_000_000))
        == 30_855_000_000
    )


# ---------------------------------------------------------------------------
# The engine and the database must agree
# ---------------------------------------------------------------------------
def test_the_engine_and_the_database_agree_on_the_translation_methods():
    from inventory.constants import TranslationMethod as DatabaseTranslationMethod

    assert {m.value for m in TranslationMethod} == set(DatabaseTranslationMethod.values)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
CONSTANTS = st.integers(min_value=0, max_value=60_000_000_000)


@given(ranges(), st.sampled_from(list(TranslationMethod)), CONSTANTS)
def test_translation_always_preserves_width(uplink, method, constant):
    translation = spec(method, constant)
    assume(_is_representable(uplink, translation))

    assert translate(uplink, translation).width_hz == uplink.width_hz


@given(ranges(), st.sampled_from(list(TranslationMethod)), CONSTANTS)
def test_translation_is_always_reversible(uplink, method, constant):
    """The property ADR-0006's two-sided reservation depends on, across every method."""
    translation = spec(method, constant)
    assume(_is_representable(uplink, translation))

    assert untranslate(translate(uplink, translation), translation) == uplink


@given(ranges(), CONSTANTS)
def test_a_reflection_always_reverses_the_edge_order(uplink, constant):
    """The uplink's *low* edge becomes the downlink's *high* edge — which is why an
    inverted side is recomputed rather than mirrored by eye."""
    translation = spec(TranslationMethod.LO_REFLECT, constant)
    assume(constant > uplink.end_hz)

    downlink = translate(uplink, translation)

    assert downlink.start_hz == constant - uplink.end_hz
    assert downlink.end_hz == constant - uplink.start_hz


def _is_representable(uplink: FrequencyRange, translation: TranslationSpec) -> bool:
    """Skip cases whose image would be negative — not a translation anyone can perform."""
    try:
        translate(uplink, translation)
    except ValueError:
        return False
    return True
