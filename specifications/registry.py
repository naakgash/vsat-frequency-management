"""Specification codes referenced by application logic.

Specification section 2: *"The stable internal code must not be freely renamed after it
is used by application logic. Admin users may edit its human-readable name, description,
help text, unit presentation, visibility, and display order without changing calculation
semantics."*

This module is the boundary between those two categories. A code listed here is
**system-managed**: the calculation engine, table column registry, importer or exporter
refers to it by name, so renaming it would silently break them. Everything *about* the
code — what it is called on screen, how it is described, its unit presentation, its
precision, whether it appears in tables — lives in the database and is admin-editable.

Adding a code here without a matching row is caught by
``tests/specifications/test_registry.py``, and vice versa.

The seed metadata below is deliberately conservative. It states what the specification
itself states and nothing more: where an engineering meaning is not fixed by the root
specification, the description is left empty for RF engineering to supply, rather than
guessed at. Empty descriptions are visible in the admin screen and reported by
``manage.py check_specifications``.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum


class DataType(StrEnum):
    """How a specification's value is stored and formatted."""

    INTEGER_HZ = "INTEGER_HZ"
    INTEGER_SPS = "INTEGER_SPS"
    DECIMAL = "DECIMAL"
    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"


class DirectionApplicability(StrEnum):
    """Which payload direction a specification applies to (section 2)."""

    FWD = "FWD"
    RTN = "RTN"
    BOTH = "BOTH"
    NOT_APPLICABLE = "NA"


@dataclasses.dataclass(frozen=True)
class SpecSeed:
    """Initial metadata for a system-managed specification code.

    Only ``code``, ``data_type`` and ``category`` are semantic. The rest is presentation
    and may be edited freely by an administrator afterwards; this is merely the starting
    point so the dictionary is usable on a fresh install.
    """

    code: str
    display_name: str
    short_name: str
    category: str
    data_type: str
    direction: str
    unit: str = ""
    display_precision: int = 0
    description: str = ""
    calculation_note: str = ""
    is_calculated: bool = False
    visible_in_tables: bool = True
    visible_in_forms: bool = False
    visible_in_detail: bool = True


# Category codes. Categories are admin-managed rows; these are the ones the seed creates.
CAT_REQUEST = "REQUEST"
CAT_BANDWIDTH = "BANDWIDTH"
CAT_FWD_RF = "FWD_RF"
CAT_RTN_RF = "RTN_RF"
CAT_IF = "IF"
CAT_GUARD = "GUARD"

CATEGORY_SEEDS: list[tuple[str, str, str, int]] = [
    # (code, name, description, display_order)
    (
        CAT_REQUEST,
        "Requested capacity",
        "Values entered by the operator when requesting capacity.",
        10,
    ),
    (CAT_BANDWIDTH, "Bandwidth", "Occupied and allocated bandwidth derived from the request.", 20),
    (CAT_GUARD, "Guard bands", "Separation applied on each side of the occupied bandwidth.", 30),
    (
        CAT_FWD_RF,
        "Forward RF",
        "Forward path radio frequencies, hub uplink and remote downlink.",
        40,
    ),
    (CAT_RTN_RF, "Return RF", "Return path radio frequencies, remote uplink and hub downlink.", 50),
    (
        CAT_IF,
        "Intermediate frequency",
        "L-band IF values derived through the equipment profile.",
        60,
    ),
]


# ---------------------------------------------------------------------------
# Codes named in specification section 2, plus the immediate derivations the same
# section implies. Display precision of 3 in MHz (kHz resolution) follows the worked
# example in section 9.5: "29,145.000-29,155.500 MHz".
# ---------------------------------------------------------------------------
SPECIFICATION_SEEDS: list[SpecSeed] = [
    # --- Requested capacity -------------------------------------------------
    SpecSeed(
        code="SYMBOL_RATE",
        display_name="Symbol rate",
        short_name="SR",
        category=CAT_REQUEST,
        data_type=DataType.INTEGER_SPS,
        direction=DirectionApplicability.BOTH,
        unit="symbols/second",
        description=(
            "Transmission symbol rate. Either the symbol rate or the occupied bandwidth "
            "is entered by the operator; the other is derived. Only one of the two is "
            "editable at a time."
        ),
        calculation_note="Symbol Rate = Occupied Bandwidth / (1 + Roll-off)",
        visible_in_forms=True,
    ),
    SpecSeed(
        code="ROLLOFF",
        display_name="Roll-off factor",
        short_name="Roll-off",
        category=CAT_REQUEST,
        data_type=DataType.DECIMAL,
        direction=DirectionApplicability.BOTH,
        unit="",
        display_precision=2,
        description=(
            "Raised-cosine roll-off factor applied to the symbol rate to obtain the "
            "occupied bandwidth. Stored as an exact decimal, never as binary floating "
            "point."
        ),
        visible_in_forms=True,
    ),
    # --- Bandwidth ----------------------------------------------------------
    SpecSeed(
        code="OCCUPIED_BANDWIDTH",
        display_name="Occupied bandwidth",
        short_name="Occupied BW",
        category=CAT_BANDWIDTH,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.BOTH,
        unit="MHz",
        display_precision=3,
        description=("Bandwidth occupied by the transmission itself, excluding guard bands."),
        calculation_note="Occupied BW = Symbol Rate x (1 + Roll-off)",
        is_calculated=True,
        visible_in_forms=True,
    ),
    SpecSeed(
        code="ALLOCATED_BANDWIDTH",
        display_name="Allocated bandwidth",
        short_name="Allocated BW",
        category=CAT_BANDWIDTH,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.BOTH,
        unit="MHz",
        display_precision=3,
        description=(
            "Total bandwidth reserved in the spectrum pool: the occupied bandwidth plus "
            "the left and right guard bands. This is the interval checked for overlap."
        ),
        calculation_note="Allocated BW = Allocated End - Allocated Start",
        is_calculated=True,
    ),
    # --- Guards -------------------------------------------------------------
    SpecSeed(
        code="LEFT_GUARD",
        display_name="Left guard band",
        short_name="Left guard",
        category=CAT_GUARD,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.BOTH,
        unit="MHz",
        display_precision=3,
        description="Separation applied below the occupied bandwidth.",
        is_calculated=True,
    ),
    SpecSeed(
        code="RIGHT_GUARD",
        display_name="Right guard band",
        short_name="Right guard",
        category=CAT_GUARD,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.BOTH,
        unit="MHz",
        display_precision=3,
        description="Separation applied above the occupied bandwidth.",
        is_calculated=True,
    ),
    # --- Forward path RF ----------------------------------------------------
    SpecSeed(
        code="FWD_HUB_UL_START_RF",
        display_name="Forward hub uplink start frequency",
        short_name="FWD hub UL start",
        category=CAT_FWD_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.FWD,
        unit="MHz",
        display_precision=3,
        description="Lower edge of the allocated range on the forward hub uplink leg.",
        calculation_note="Allocated Start = Centre - Occupied BW / 2 - Left Guard",
        is_calculated=True,
    ),
    SpecSeed(
        code="FWD_HUB_UL_CENTER_RF",
        display_name="Forward hub uplink centre frequency",
        short_name="FWD hub UL centre",
        category=CAT_FWD_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.FWD,
        unit="MHz",
        display_precision=3,
        description="Centre frequency of the transmission on the forward hub uplink leg.",
        is_calculated=True,
    ),
    SpecSeed(
        code="FWD_HUB_UL_END_RF",
        display_name="Forward hub uplink end frequency",
        short_name="FWD hub UL end",
        category=CAT_FWD_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.FWD,
        unit="MHz",
        display_precision=3,
        description=(
            "Upper edge of the allocated range on the forward hub uplink leg. Ranges are "
            "half-open, so this edge is exclusive."
        ),
        calculation_note="Allocated End = Centre + Occupied BW / 2 + Right Guard",
        is_calculated=True,
    ),
    SpecSeed(
        code="FWD_REMOTE_DL_CENTER_RF",
        display_name="Forward remote downlink centre frequency",
        short_name="FWD remote DL centre",
        category=CAT_FWD_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.FWD,
        unit="MHz",
        display_precision=3,
        description=(
            "Centre frequency on the forward remote downlink leg, derived from the hub "
            "uplink centre through the payload path translation."
        ),
        # Deliberately empty: the translation method and constant are per payload path
        # and unconfirmed (OQ-02). Supplying a formula here would be inventing one.
        calculation_note="",
        is_calculated=True,
    ),
    # --- Return path RF -----------------------------------------------------
    SpecSeed(
        code="RTN_REMOTE_UL_CENTER_RF",
        display_name="Return remote uplink centre frequency",
        short_name="RTN remote UL centre",
        category=CAT_RTN_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.RTN,
        unit="MHz",
        display_precision=3,
        description="Centre frequency of the transmission on the return remote uplink leg.",
        is_calculated=True,
    ),
    SpecSeed(
        code="RTN_HUB_DL_CENTER_RF",
        display_name="Return hub downlink centre frequency",
        short_name="RTN hub DL centre",
        category=CAT_RTN_RF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.RTN,
        unit="MHz",
        display_precision=3,
        description=(
            "Centre frequency on the return hub downlink leg, derived from the remote "
            "uplink centre through the payload path translation."
        ),
        calculation_note="",  # OQ-02, as above.
        is_calculated=True,
    ),
    # --- Intermediate frequency ---------------------------------------------
    SpecSeed(
        code="L_BAND_CENTER_IF",
        display_name="L-band centre IF",
        short_name="L-band centre IF",
        category=CAT_IF,
        data_type=DataType.INTEGER_HZ,
        direction=DirectionApplicability.BOTH,
        unit="MHz",
        display_precision=3,
        description=(
            "Centre intermediate frequency on the hub-side equipment, derived from the "
            "hub-side RF through the selected equipment profile."
        ),
        # The conversion depends on the profile's method and sideband; the LO values
        # themselves are OQ-04 and are not seeded.
        calculation_note="",
        is_calculated=True,
    ),
]

#: Codes the application refers to by name. Renaming one requires a code change.
SYSTEM_CODES: frozenset[str] = frozenset(seed.code for seed in SPECIFICATION_SEEDS)


def seed_for(code: str) -> SpecSeed | None:
    """Return the seed metadata for a system-managed code."""
    for seed in SPECIFICATION_SEEDS:
        if seed.code == code:
            return seed
    return None
