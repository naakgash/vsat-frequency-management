"""Beam enumerations and capability codenames.

``Direction`` and ``SpectrumLeg`` are **imported** from ``inventory.constants`` rather than
mirrored: ``beams`` sits above ``inventory``, so it may import it directly. The engine's
copies exist only because ``calculations`` sits *below* and cannot (ADR-0010).
"""

from __future__ import annotations

from django.db import models

from inventory.constants import Direction, SpectrumLeg

__all__ = [
    "ACTIVATION_BLOCKED",
    "BEAM_ACTIVATED",
    "BEAM_CREATED",
    "BEAM_DEACTIVATED",
    "BEAM_UPDATED",
    "BEAM_VALIDATED",
    "CANONICAL_LEG_DEFAULTS",
    "MANAGE_BEAMS",
    "VIEW_BEAMS",
    "ConfigurationState",
    "Direction",
    "SpectrumLeg",
    "ValidationOutcome",
]


class ConfigurationState(models.TextChoices):
    """How complete and how correct a Beam's configuration is. Specification section 10.1.

    Three states rather than a boolean, because "not finished" and "finished but wrong" call
    for different things from the person looking at it. A half-built Beam needs the rest of
    the wizard; an invalid one needs a decision.
    """

    INCOMPLETE = "INCOMPLETE", "Incomplete — not every enabled direction is configured"
    INVALID = "INVALID", "Invalid — configured, but a rule is broken"
    VALID = "VALID", "Valid — every enabled direction passes"


class ValidationOutcome(models.TextChoices):
    """The result of one Beam Builder validation run."""

    PASSED = "PASSED", "Passed"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS", "Passed with warnings"
    FAILED = "FAILED", "Failed"


#: Which leg the operator enters the centre frequency on, by direction (**A-07**).
#:
#: Uplink-canonical in both directions, which is what an operator planning a transmission
#: usually knows first. It is *configuration*, stored per Beam direction, so changing it for
#: a particular Beam is an edit rather than a code change — and **OQ-28** may change the
#: default without changing anything else.
CANONICAL_LEG_DEFAULTS: dict[str, str] = {
    Direction.FWD: SpectrumLeg.HUB_UPLINK,
    Direction.RTN: SpectrumLeg.REMOTE_UPLINK,
}

# --- Capabilities (docs/design/03 section 2.2) ------------------------------
VIEW_BEAMS = "beams.view_beam"
#: Beam engineering is administrator-only (§25). An Operator selects a Beam when creating a
#: Satnet Path; they never configure one.
MANAGE_BEAMS = "beams.manage_beams"

# --- Audit actions ----------------------------------------------------------
BEAM_CREATED = "BEAM_CREATED"
BEAM_UPDATED = "BEAM_UPDATED"
BEAM_VALIDATED = "BEAM_VALIDATED"
BEAM_ACTIVATED = "BEAM_ACTIVATED"
BEAM_DEACTIVATED = "BEAM_DEACTIVATED"
#: Recorded when activation is refused. §26.6 makes the refusal a requirement, and a refusal
#: nobody can find afterwards is indistinguishable from the button not working.
ACTIVATION_BLOCKED = "BEAM_ACTIVATION_BLOCKED"
