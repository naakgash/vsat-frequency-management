"""Inventory enumerations and capability codenames.

Enumerations are Python ``TextChoices`` mirrored by PostgreSQL CHECK constraints rather
than native enum types: altering a native enum inside a transaction is restricted, and a
CHECK produces a clearer error (docs/design/02 section 9).
"""

from __future__ import annotations

from django.db import models


class OrbitType(models.TextChoices):
    """Specification section 13.1."""

    GEO = "GEO", "Geostationary"
    MEO = "MEO", "Medium Earth orbit"
    LEO = "LEO", "Low Earth orbit"


class PolarizationType(models.TextChoices):
    """Polarization types a Band may allow.

    All four standard forms are modelled. **Which of them are actually in use is OQ-14**
    and is not decided here: a Band allows the ones an administrator selects, and no
    Band ships with any preselected.
    """

    RHCP = "RHCP", "Right-hand circular"
    LHCP = "LHCP", "Left-hand circular"
    H = "H", "Linear horizontal"
    V = "V", "Linear vertical"


class EquipmentType(models.TextChoices):
    """Specification section 13.5."""

    BUC = "BUC", "Block up-converter"
    BDC = "BDC", "Block down-converter"
    LNB = "LNB", "Low-noise block down-converter"
    OTHER = "OTHER", "Other"


class ConversionMethod(models.TextChoices):
    """How a profile maps between RF and IF.

    Specification section 13.5 requires ``RF = LO + IF``, ``RF = LO - IF``,
    ``IF = |RF - LO|`` and a fixed translation offset. The absolute-value form is not a
    separate method: it is what ``LO_MINUS_IF`` computes in the down-conversion
    direction, and it is only invertible because :class:`Sideband` records which side the
    local oscillator sits on. That is why the two fields exist together.
    """

    LO_PLUS_IF = "LO_PLUS_IF", "RF = LO + IF"
    LO_MINUS_IF = "LO_MINUS_IF", "RF = LO - IF"
    FIXED_OFFSET = "FIXED_OFFSET", "RF = IF + fixed offset"


class Sideband(models.TextChoices):
    """Which side of the RF the local oscillator sits on.

    Low-side injection is non-inverting; high-side injection inverts the spectrum. See
    docs/design/02 section 2.3.
    """

    LOW_SIDE = "LOW_SIDE", "Low-side injection (LO below RF)"
    HIGH_SIDE = "HIGH_SIDE", "High-side injection (LO above RF)"


# --- Capabilities (docs/design/03 section 2.2) ------------------------------
VIEW_INVENTORY = "inventory.view_satellite"
MANAGE_INVENTORY = "inventory.manage_inventory"

# --- Audit actions ----------------------------------------------------------
INVENTORY_CREATED = "INVENTORY_CREATED"
INVENTORY_UPDATED = "INVENTORY_UPDATED"
INVENTORY_DEACTIVATED = "INVENTORY_DEACTIVATED"
INVENTORY_REACTIVATED = "INVENTORY_REACTIVATED"


class Direction(models.TextChoices):
    """Payload direction. Specification section 13.7."""

    FWD = "FWD", "Forward"
    RTN = "RTN", "Return"


class SpectrumLeg(models.TextChoices):
    """One side of one direction's payload chain.

    Specification section 13.6 calls this a Frequency Window "side"; sections 8.1 and
    13.11 call it a "leg". They are the same concept, modelled once (**A-02**).

    A FWD path runs ``HUB_UPLINK`` to ``REMOTE_DOWNLINK``; a RTN path runs
    ``REMOTE_UPLINK`` to ``HUB_DOWNLINK`` (**A-03**).
    """

    HUB_UPLINK = "HUB_UPLINK", "Hub uplink"
    REMOTE_DOWNLINK = "REMOTE_DOWNLINK", "Remote downlink"
    REMOTE_UPLINK = "REMOTE_UPLINK", "Remote uplink"
    HUB_DOWNLINK = "HUB_DOWNLINK", "Hub downlink"


class TranslationMethod(models.TextChoices):
    """How a Payload Path maps an uplink frequency to its downlink frequency.

    Specification section 13.7 requires a deterministic mapping and offers "translation
    offset or satellite LO". Both shapes are modelled:

    * ``OFFSET_ADD``      — ``downlink = uplink + constant``
    * ``OFFSET_SUBTRACT`` — ``downlink = uplink - constant``
    * ``LO_REFLECT``      — ``downlink = constant - uplink``, which inverts the spectrum

    Which method and constant applies to any real payload is **OQ-02**. Nothing is seeded.
    """

    OFFSET_ADD = "OFFSET_ADD", "Downlink = uplink + offset"
    OFFSET_SUBTRACT = "OFFSET_SUBTRACT", "Downlink = uplink - offset"
    LO_REFLECT = "LO_REFLECT", "Downlink = constant - uplink (inverting)"


class GuardMode(models.TextChoices):
    """How a Guard Policy computes the separation either side of a transmission.

    Values are **OQ-07**; only the shapes are fixed here.
    """

    FIXED = "FIXED", "Fixed width"
    PERCENT_OF_OCCUPIED = "PERCENT_OF_OCCUPIED", "Percentage of occupied bandwidth"
    MAX_OF_FIXED_AND_PERCENT = "MAX_OF_FIXED_AND_PERCENT", "Greater of fixed and percentage"


class SpectrumResourceKind(models.TextChoices):
    """What kind of shared thing a Spectrum Resource records. **OQ-25**, ADR-0018.

    Taken from the three things RF engineering's answer names as creating competition, and
    deliberately not extended beyond them. The value is descriptive: the overlap constraint
    keys on the resource's identity, never on this field, so adding a kind later cannot
    change what conflicts with what.
    """

    PAYLOAD_INPUT = "PAYLOAD_INPUT", "Shared satellite payload input"
    RF_CHAIN = "RF_CHAIN", "Shared RF chain"
    BEAM_PLAN = "BEAM_PLAN", "Approved Beam frequency and polarization plan"


# --- Audit actions for versioned master data --------------------------------
MASTER_DATA_VERSIONED = "MASTER_DATA_VERSIONED"
