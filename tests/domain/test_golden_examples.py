"""Worked examples supplied by RF engineering. Specification section 24 — **OQ-22**.

Everything else in ``tests/domain/`` proves the engine is *self-consistent*: widths survive
translation, rounding goes outward, a round trip returns what it started with. None of it
proves the answers are the ones RF engineering would give. Only a worked example whose
numbers came from outside the codebase can do that.

**The directory is empty, and that is the honest state.** Inventing a plausible example
would go green, look like validation, and check the engine against itself with extra steps
— which is exactly what §26.20 forbids.

The file format, one JSON object per example::

    {
      "name": "FWD Ka-band, 10 Msps",
      "source": "RF engineering, J. Doe, 2026-05-14",   // required
      "direction": "FWD",
      "symbol_rate_sps": 10000000,
      "rolloff": "0.35",
      "uplink_centre_hz": 29145000000,
      "guard": {"mode": "FIXED", "left_hz": 1000000, "right_hz": 1000000},
      "translation": {"method": "OFFSET_SUBTRACT", "constant_hz": 10000000000},
      "expect": {
        "occupied_bandwidth_hz": 13500000,
        "uplink_occupied": [29138250000, 29151750000],
        "uplink_allocated": [29137250000, 29152750000],
        "downlink_occupied": [19138250000, 19151750000],
        "downlink_allocated": [19137250000, 19152750000]
      }
    }

``source`` is required and is not ceremony: an example whose provenance nobody can state is
indistinguishable from one somebody made up, which is the thing this directory exists to
avoid.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from calculations import bandwidth, guards
from calculations.ranges import FrequencyRange
from calculations.translation import TranslationMethod, TranslationSpec
from calculations.types import BandwidthRequest, GuardMode, GuardPolicySpec, GuardSource

GOLDEN_DIR = Path(__file__).parent / "golden"

#: Set by the Phase 9 pipeline. Until then the absence is a skip; from then it is a failure.
REQUIRE_FLAG = "VSAT_REQUIRE_GOLDEN_EXAMPLES"


def _example_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.json"))


def _required() -> bool:
    return os.environ.get(REQUIRE_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_the_golden_directory_exists_and_is_documented():
    """The harness is wired up before the data arrives, so adding an example is dropping in
    a file rather than writing a test."""
    assert GOLDEN_DIR.is_dir()
    assert (GOLDEN_DIR / "README.md").exists()


def test_golden_examples_are_present_when_they_are_required():
    """OQ-22's deadline, expressed as a test.

    While the flag is unset this reports the gap and moves on. Phase 9 sets it, and from
    that point an empty directory fails the build. That is what stops "we will add them
    later" from quietly becoming "we shipped without them".
    """
    if _example_files():
        return

    message = (
        "No golden worked examples in tests/domain/golden/. The engine is only proven "
        "self-consistent, never correct against RF engineering's own figures (section 24, "
        "OQ-22)."
    )
    if _required():
        pytest.fail(message + f" {REQUIRE_FLAG} is set, so this is now a failure.")
    pytest.skip(message + f" Set {REQUIRE_FLAG}=1 to make this a failure.")


# ---------------------------------------------------------------------------
# The examples themselves
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.stem)
def test_a_golden_example_matches_the_engine(path: Path):
    """Run one supplied example through the engine and compare every stated value."""
    example = json.loads(path.read_text(encoding="utf-8"))

    assert example.get("source"), (
        f"{path.name} has no 'source'. An example whose provenance nobody can state is "
        f"indistinguishable from one somebody made up."
    )

    placement = _run(example)
    expected = example["expect"]

    if "occupied_bandwidth_hz" in expected:
        assert placement.uplink.occupied_bandwidth_hz == expected["occupied_bandwidth_hz"]
    for key, actual in (
        ("uplink_occupied", placement.uplink.occupied),
        ("uplink_allocated", placement.uplink.allocated),
        ("downlink_occupied", placement.downlink.occupied),
        ("downlink_allocated", placement.downlink.allocated),
    ):
        if key in expected:
            assert actual == FrequencyRange(*expected[key]), key


def _run(example: dict):
    """Build the engine inputs from an example file."""
    translation = TranslationSpec(
        method=TranslationMethod(example["translation"]["method"]),
        constant_hz=example["translation"]["constant_hz"],
        spectral_inversion=example["translation"].get("spectral_inversion", False),
    )
    request = BandwidthRequest(
        rolloff=Decimal(example["rolloff"]),
        symbol_rate_sps=example.get("symbol_rate_sps"),
        occupied_bandwidth_hz=example.get("occupied_bandwidth_hz"),
    )
    _, occupied_bw = bandwidth.resolve_request(request)
    return bandwidth.place_both_sides(
        request=request,
        centre_hz=example["uplink_centre_hz"],
        guards=guards.resolve(_guard_policy(example.get("guard")), occupied_bw),
        translation=translation,
    )


def _guard_policy(guard: dict | None) -> GuardPolicySpec | None:
    if not guard:
        return None
    return GuardPolicySpec(
        mode=GuardMode(guard["mode"]),
        source=GuardSource.OVERRIDE,
        label="golden example",
        fixed_left_hz=guard.get("left_hz"),
        fixed_right_hz=guard.get("right_hz"),
        percent_left=Decimal(guard["percent_left"]) if "percent_left" in guard else None,
        percent_right=Decimal(guard["percent_right"]) if "percent_right" in guard else None,
    )
