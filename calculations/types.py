"""Value types the engine consumes and returns.

Plain frozen dataclasses and ``StrEnum``, never Django models. That is what lets this
package sit at the bottom of the module graph and be exercised by Hypothesis without a
database — the property tests in ``tests/domain/`` construct thousands of these per run.

The guard enumerations are duplicated from ``inventory.constants`` on purpose:
``calculations`` cannot import ``inventory`` (the dependency runs the other way), and
mirroring three string constants is a smaller price than inverting the layering.
``tests/domain/test_guards.py`` compares the two member-for-member, so a value added on one
side and not the other fails the build rather than silently resolving to no guard.
"""

from __future__ import annotations

import dataclasses
import enum
from decimal import Decimal

from calculations.ranges import FrequencyRange


class GuardMode(enum.StrEnum):
    """How a guard policy computes its widths. Mirrors ``inventory.constants.GuardMode``."""

    FIXED = "FIXED"
    PERCENT_OF_OCCUPIED = "PERCENT_OF_OCCUPIED"
    MAX_OF_FIXED_AND_PERCENT = "MAX_OF_FIXED_AND_PERCENT"


class GuardSource(enum.StrEnum):
    """Where a resolved guard came from. Specification section 9.2, ADR-0016.

    Carried through to the result because §9.2 lets an operator *"select or accept"* a
    policy: accepting one you cannot see the origin of is not a decision. It is also what a
    reviewer needs in order to tell an explicit override from an inherited default.
    """

    OVERRIDE = "OVERRIDE"
    SATNET = "SATNET"
    WINDOW = "WINDOW"
    SYSTEM = "SYSTEM"
    NONE = "NONE"


@dataclasses.dataclass(frozen=True)
class GuardPolicySpec:
    """A guard policy reduced to what the engine needs to evaluate it.

    Built from an ``inventory.GuardPolicy`` row by the caller. The engine never sees the
    model, so it cannot accidentally depend on a field that only exists in the database.
    """

    mode: GuardMode
    source: GuardSource
    label: str = ""
    fixed_left_hz: int | None = None
    fixed_right_hz: int | None = None
    percent_left: Decimal | None = None
    percent_right: Decimal | None = None

    def __post_init__(self) -> None:
        required: dict[GuardMode, tuple[str, ...]] = {
            GuardMode.FIXED: ("fixed_left_hz", "fixed_right_hz"),
            GuardMode.PERCENT_OF_OCCUPIED: ("percent_left", "percent_right"),
            GuardMode.MAX_OF_FIXED_AND_PERCENT: (
                "fixed_left_hz",
                "fixed_right_hz",
                "percent_left",
                "percent_right",
            ),
        }
        # The same rule as ck_guard_mode_has_required_values in the database. Checked here
        # too because the engine is reachable from the importer and from a management
        # command, neither of which goes through a form: a policy missing its own values
        # would otherwise resolve to a zero guard silently, which is the one failure mode a
        # guard must not have.
        missing = [f for f in required[self.mode] if getattr(self, f) is None]
        if missing:
            raise ValueError(
                f"A {self.mode} guard policy needs {', '.join(required[self.mode])}; "
                f"missing {', '.join(missing)}."
            )
        for field in ("fixed_left_hz", "fixed_right_hz", "percent_left", "percent_right"):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValueError(f"{field} cannot be negative: {value}")


@dataclasses.dataclass(frozen=True)
class GuardWidths:
    """The resolved separation either side of the occupied bandwidth, in whole Hz."""

    left_hz: int
    right_hz: int
    source: GuardSource = GuardSource.NONE
    policy_label: str = ""

    @property
    def total_hz(self) -> int:
        return self.left_hz + self.right_hz


@dataclasses.dataclass(frozen=True)
class BandwidthRequest:
    """What an operator asked for. Specification section 9.2.

    Exactly one of ``symbol_rate_sps`` and ``occupied_bandwidth_hz`` is supplied — §9.2
    requires both entry modes and states that only one of the two is editable at a time, so
    accepting both would leave the engine deciding which one the operator meant. **OQ-05**
    is only about which one is *pre-selected*, not about whether both exist.
    """

    rolloff: Decimal
    symbol_rate_sps: int | None = None
    occupied_bandwidth_hz: int | None = None

    def __post_init__(self) -> None:
        supplied = [
            name
            for name in ("symbol_rate_sps", "occupied_bandwidth_hz")
            if getattr(self, name) is not None
        ]
        if len(supplied) != 1:
            raise ValueError(
                "Supply exactly one of symbol_rate_sps or occupied_bandwidth_hz; the "
                f"other is derived (section 9.2). Got: {supplied or 'neither'}."
            )
        if self.rolloff < 0:
            raise ValueError(f"Roll-off cannot be negative: {self.rolloff}")
        for name in supplied:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive: {getattr(self, name)}")


@dataclasses.dataclass(frozen=True)
class Placement:
    """A fully resolved placement: what is transmitted and what is reserved for it.

    The name is deliberate. §4 and §7 forbid the traditional label for this concept
    anywhere in the model, the API, the URLs or the interface, and
    ``tests/ui/test_terminology.py`` enforces that — down to a docstring, as writing this
    one demonstrated. A direction-specific allocation is a **Satnet Path**; a ``Placement``
    is the arithmetic behind one, with no identity and no lifecycle of its own.
    """

    symbol_rate_sps: int
    rolloff: Decimal
    occupied_bandwidth_hz: int
    occupied: FrequencyRange
    allocated: FrequencyRange
    guards: GuardWidths

    @property
    def allocated_bandwidth_hz(self) -> int:
        """What the overlap constraint actually compares. Includes the guards."""
        return self.allocated.width_hz

    @property
    def centre_hz(self) -> int:
        """The requested centre, recovered exactly from the occupied range.

        Exact even for an odd bandwidth: the range was built symmetrically around this
        value, so its width is even and the midpoint is a whole Hz.
        """
        return self.occupied.start_hz + self.occupied.width_hz // 2
