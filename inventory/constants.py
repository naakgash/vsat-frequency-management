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
