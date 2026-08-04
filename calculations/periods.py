"""Half-open time periods, and the intersection that bounds an allocation. **OQ-32**, ADR-0020.

The time-axis twin of :mod:`calculations.ranges`, and deliberately the same shape: half-open
``[start, end)`` (§8.4, §14.5, **A-10**), so a period ending exactly where the next begins does
not overlap it and needs no special case.

One difference the RF axis does not have: **the upper bound may be absent**, and absent means
*open-ended*, not *unknown*. A Beam with no end date is valid indefinitely. Treating ``None`` as
zero, or as "some far future date", both go wrong — the first forbids everything, the second
silently invents an expiry nobody agreed. It is modelled as genuine infinity here and nowhere
else has to remember.

Pure, Django-free, and property-tested for the reason ``docs/design/01`` gives about every
module under ``calculations``: the intersection of three periods is exactly the kind of
arithmetic that looks obvious and has an off-by-one in the open-ended case.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime


@dataclasses.dataclass(frozen=True)
class TimePeriod:
    """``[start, end)``, where ``end is None`` means open-ended.

    Frozen because a period is a value: two periods with the same bounds are the same period,
    and nothing should be able to narrow one in place while another object holds a reference.
    """

    start: datetime
    end: datetime | None = None

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"A period must run forwards: {self.start} to {self.end}")

    @property
    def is_open_ended(self) -> bool:
        return self.end is None

    def contains(self, other: TimePeriod) -> bool:
        """Does ``other`` sit entirely inside this period?

        The open-ended cases are the ones worth reading twice. An open-ended period contains
        an open-ended one; a **bounded** period does not — a Beam that expires in March cannot
        contain an allocation that never ends, and that asymmetry is the whole of the OQ-32
        rule for the common case where somebody leaves the end date blank.
        """
        if other.start < self.start:
            return False
        if self.end is None:
            return True
        if other.end is None:
            return False
        return other.end <= self.end

    def overlaps(self, other: TimePeriod) -> bool:
        if self.end is not None and other.start >= self.end:
            return False
        if other.end is not None and self.start >= other.end:
            return False
        return True


def intersect(periods: list[TimePeriod]) -> TimePeriod | None:
    """The period common to all of them, or ``None`` if they do not all overlap.

    **OQ-32**: *"The maximum permitted period of a Satnet Path is therefore the intersection of
    those three periods."* This computes that maximum, and returning ``None`` rather than an
    empty period is deliberate — "there is no moment when all three are valid" is a different
    statement from "the window is zero wide", and only the first is true when a Beam expires
    before its Satnet begins.

    An empty input has no constraints to satisfy and therefore no answer; callers pass at least
    one period, and the ``ValueError`` says so rather than inventing an unbounded result.
    """
    if not periods:
        raise ValueError("intersect() needs at least one period; there is no unbounded default")

    start = max(period.start for period in periods)
    ends = [period.end for period in periods if period.end is not None]
    end = min(ends) if ends else None

    if end is not None and end <= start:
        return None
    return TimePeriod(start, end)
