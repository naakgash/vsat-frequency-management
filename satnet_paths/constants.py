"""Satnet Path enumerations, capabilities and audit actions. §9, §15.2.

**"Satnet Path" is the only term for a direction-specific allocation** (§7, **A-19**). The
forbidden alternative is not used anywhere, and a repository-wide guard rail enforces that on
every commit.
"""

from __future__ import annotations

from django.db import models

from spectrum.constants import ReservationStatus


class InputMode(models.TextChoices):
    """How the operator sized the transmission. §9.2.

    Both are offered and each derives the other, so exactly one is stored along with the value
    the operator actually typed. Storing both independently would let them drift, and §9.2
    forbids them being independently editable for that reason.
    """

    OCCUPIED_BW = "OCCUPIED_BW", "Occupied bandwidth"
    SYMBOL_RATE = "SYMBOL_RATE", "Symbol rate"


#: The §15.2 lifecycle. Reused from ``spectrum`` rather than redeclared: the reservation's
#: status column and this one must agree, and two enumerations with the same members are two
#: places to add a ninth.
PathStatus = ReservationStatus

VIEW_SATNET_PATH = "satnet_paths.view_satnetpath"
MANAGE_SATNET_PATHS = "satnet_paths.manage_satnet_paths"

PATH_CREATED = "SATNET_PATH_CREATED"
PATH_UPDATED = "SATNET_PATH_UPDATED"
PATH_BLOCKED = "SATNET_PATH_BLOCKED"
