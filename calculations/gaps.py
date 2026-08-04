"""Free capacity: what is left of an entitlement after the reservations. §16, ADR-0009.

**Nothing here is stored.** §16 is explicit that free capacity is *calculated*, and a stored
free-capacity table is the classic way to be confidently wrong: the moment a reservation is
written, committed and then rolled back, or written by a path the cache does not know about,
the stored answer and the reservations disagree and nothing says which is right.

The engine is pure — ranges in, ranges out, no ORM — for the reason ``docs/design/01`` gives
about every module under ``calculations``: it is the highest-risk arithmetic in the product and
it is worth being able to property-test it without a database.

**Bounds come from the Beam's active assignments, not from its Frequency Window** (ADR-0019).
That is not a detail:

    *"The free-capacity engine shall calculate available capacity only within the active Beam
    assignments and not across the complete Payload Path Window."*

A window may be shared between Beams. Reporting gaps across the whole of it would offer an
operator spectrum that belongs to somebody else, and the offer would look authoritative.
"""

from __future__ import annotations

import dataclasses

from calculations.ranges import FrequencyRange


@dataclasses.dataclass(frozen=True)
class Gap:
    """One free interval, and how it sits relative to what is already there."""

    range: FrequencyRange
    #: The nearest occupied edge below this gap, if any. An operator placing a transmission
    #: wants to know what they are next to, not only that there is room.
    below_hz: int | None = None
    above_hz: int | None = None

    @property
    def width_hz(self) -> int:
        return self.range.width_hz

    def fits(self, width_hz: int) -> bool:
        return self.width_hz >= width_hz


@dataclasses.dataclass(frozen=True)
class CapacitySummary:
    """What §16 asks a screen to be able to say."""

    total_hz: int
    free_hz: int
    gaps: tuple[Gap, ...]

    @property
    def used_hz(self) -> int:
        return self.total_hz - self.free_hz

    @property
    def largest_gap_hz(self) -> int:
        return max((gap.width_hz for gap in self.gaps), default=0)

    @property
    def utilisation(self) -> float:
        """Fraction of the entitlement that is reserved, 0.0 to 1.0.

        A float, and the only one in the engineering path — deliberately. It is a display
        figure and nothing is derived from it; every value that *is* derived from it stays in
        integer Hz (**A-08**). A percentage computed in Decimal and then rendered to one
        decimal place would be precision theatre.
        """
        return 0.0 if self.total_hz == 0 else self.used_hz / self.total_hz


def subtract(entitlement: FrequencyRange, occupied: list[FrequencyRange]) -> list[FrequencyRange]:
    """What is left of ``entitlement`` once every ``occupied`` range is removed.

    Half-open throughout, which is what makes this arithmetic rather than a special case:
    a reservation ending exactly where the next begins leaves no gap between them, and one
    ending exactly at the entitlement's upper edge leaves nothing above it (**A-11**).
    """
    free: list[FrequencyRange] = []
    cursor = entitlement.start_hz

    for taken in sorted(occupied, key=lambda r: r.start_hz):
        if taken.end_hz <= entitlement.start_hz or taken.start_hz >= entitlement.end_hz:
            continue  # Wholly outside this entitlement.
        start = max(taken.start_hz, entitlement.start_hz)
        if start > cursor:
            free.append(FrequencyRange(cursor, start))
        cursor = max(cursor, min(taken.end_hz, entitlement.end_hz))

    if cursor < entitlement.end_hz:
        free.append(FrequencyRange(cursor, entitlement.end_hz))
    return free


def find(
    entitlements: list[FrequencyRange],
    occupied: list[FrequencyRange],
    *,
    minimum_width_hz: int = 1,
) -> list[Gap]:
    """Every free interval across every entitlement, in ascending order.

    ``entitlements`` is a list because a Beam direction may hold several assignments
    (**OQ-27**). Gaps are found **within** each one and never merged across two: two adjacent
    assignments are two separate grants, and a transmission spanning the join would sit
    partly outside whichever one expires first.
    """
    gaps: list[Gap] = []
    taken = sorted(occupied, key=lambda r: r.start_hz)

    for entitlement in sorted(entitlements, key=lambda r: r.start_hz):
        for free in subtract(entitlement, taken):
            if free.width_hz < minimum_width_hz:
                continue
            gaps.append(
                Gap(
                    range=free,
                    below_hz=_nearest_below(free.start_hz, taken),
                    above_hz=_nearest_above(free.end_hz, taken),
                )
            )
    return sorted(gaps, key=lambda gap: gap.range.start_hz)


def summarise(
    entitlements: list[FrequencyRange],
    occupied: list[FrequencyRange],
) -> CapacitySummary:
    """Total, free, and the gaps — the three numbers §16 asks a screen to show.

    ``total`` is the sum of the entitlements, so a Beam entitled to two 20 MHz sub-ranges has
    40 MHz of capacity even though the window between them is 200 MHz wide. Anything else
    would report utilisation against spectrum the Beam may not use.
    """
    gaps = find(entitlements, occupied)
    return CapacitySummary(
        total_hz=sum(entitlement.width_hz for entitlement in entitlements),
        free_hz=sum(gap.width_hz for gap in gaps),
        gaps=tuple(gaps),
    )


def first_fit(
    entitlements: list[FrequencyRange],
    occupied: list[FrequencyRange],
    width_hz: int,
) -> Gap | None:
    """The lowest gap that fits ``width_hz``, or ``None``.

    Lowest-first is a *proposal* strategy and deliberately the dullest one available: it is
    deterministic, it is explainable to an operator in one sentence, and it packs from one
    end rather than fragmenting the middle. §9.3 requires Auto-place to propose and never
    save, so being predictable matters more here than being clever.
    """
    return next((gap for gap in find(entitlements, occupied) if gap.fits(width_hz)), None)


def _nearest_below(edge_hz: int, occupied: list[FrequencyRange]) -> int | None:
    ends = [taken.end_hz for taken in occupied if taken.end_hz <= edge_hz]
    return max(ends) if ends else None


def _nearest_above(edge_hz: int, occupied: list[FrequencyRange]) -> int | None:
    starts = [taken.start_hz for taken in occupied if taken.start_hz >= edge_hz]
    return min(starts) if starts else None
