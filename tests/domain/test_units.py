"""Frequency unit conversion.

ADR-0003. Every one of these is about the same failure: a frequency that is a fraction of
a Hz away from the one someone entered. Nothing shows that at the time — it surfaces much
later as an exclusion constraint rejecting two intervals that look adjacent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.template import Context, Template

from calculations import units


@pytest.mark.parametrize(
    ("megahertz", "expected_hz"),
    [
        ("29145", 29_145_000_000),
        ("29145.5", 29_145_500_000),
        ("29145.000001", 29_145_000_001),
        ("0", 0),
        ("0.000001", 1),
    ],
)
def test_megahertz_converts_to_whole_hertz(megahertz, expected_hz):
    assert units.to_hz(Decimal(megahertz)) == expected_hz


def test_a_value_finer_than_one_hertz_is_refused_rather_than_rounded():
    """Rounding would be invisible, and the caller would get a frequency that is *nearly*
    the one they asked for."""
    with pytest.raises(units.SubHertzError):
        units.to_hz(Decimal("29145.0000005"))


def test_conversion_never_goes_through_binary_floating_point():
    """§14.1. The classic float failure, applied to a frequency: 0.1 + 0.2 != 0.3.

    ``29145.1 + 0.2`` as a float is 29145.299999999999, which converts to 29,145,299,999
    Hz — one Hz short, from arithmetic that looks exact.
    """
    exact = units.to_hz(Decimal("29145.1") + Decimal("0.2"))

    assert exact == 29_145_300_000
    assert units.to_mhz(exact) == Decimal("29145.3")


def test_a_round_trip_is_lossless():
    for hz in (29_145_000_000, 1, 0, 29_145_000_001, 30_000_000_000):
        assert units.to_hz(units.to_mhz(hz)) == hz


def test_hertz_is_wide_enough_for_the_ka_band():
    """A 32-bit signed column would overflow on the first real Ka-band frequency."""
    ka_uplink = units.to_hz(Decimal("30000"))

    assert ka_uplink == 30_000_000_000
    assert ka_uplink > 2**31 - 1


def test_to_mhz_passes_none_through():
    assert units.to_mhz(None) is None


# ---------------------------------------------------------------------------
# The display filter
# ---------------------------------------------------------------------------
def _render(template: str, **context) -> str:
    return Template("{% load rf %}" + template).render(Context(context))


@pytest.mark.parametrize(
    ("hz", "expected"),
    [
        (29_145_000_000, "29,145.000"),
        (29_145_500_000, "29,145.500"),
        (500_000, "0.500"),
        (0, "0.000"),
    ],
)
def test_the_mhz_filter_renders_megahertz_with_separators(hz, expected):
    assert _render("{{ value|mhz }}", value=hz) == expected


def test_the_mhz_filter_can_be_asked_for_full_hertz_precision():
    assert _render("{{ value|mhz:6 }}", value=29_145_000_001) == "29,145.000001"


def test_the_mhz_filter_renders_an_absent_value_as_an_em_dash():
    """An optional column — a guard policy's unused half, say — must not render as 0.000,
    which would read as a guard of nothing rather than no guard at all."""
    assert _render("{{ value|mhz }}", value=None) == "—"


def test_the_hz_filter_renders_raw_hertz_with_separators():
    assert _render("{{ value|hz }}", value=29_145_000_000) == "29,145,000,000"


# ---------------------------------------------------------------------------
# The two display paths must agree
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_dictionary_and_column_display_paths_produce_the_same_number(seeded_dictionary):
    """ADR-0003 keeps two display conversions, for the reason recorded there: one is
    driven by a unit an administrator recorded, the other by a column that is Hz by
    construction, and ``specifications`` sits below ``inventory`` so they cannot share
    code.

    Two implementations of one arithmetic rule is exactly the situation where the rule
    drifts, so they are compared here rather than trusted.
    """
    from specifications.templatetags.spec_tags import spec_value

    hz = 29_145_500_000
    # FWD_HUB_UL_CENTER_RF is seeded with unit MHz and INTEGER_HZ storage.
    through_dictionary = spec_value({}, hz, "FWD_HUB_UL_CENTER_RF", with_unit=False)
    through_column = _render("{{ value|mhz }}", value=hz)

    assert through_dictionary == through_column == "29,145.500"
