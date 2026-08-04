"""Half-open frequency intervals. Specification sections 8.4 and 25, assumption **A-10**.

Every interval in the platform is ``[start, end)`` — the lower edge belongs to it, the
upper edge does not. That single convention is what makes adjacency decidable: ``[0, 100)``
and ``[100, 200)`` provably do not overlap, so §25's *"adjacency is legal"* needs no
tolerance and no epsilon. Any physical separation is a guard band, never an implicit gap
(**A-11**).

The type is immutable. An interval that could be widened in place would let a validated
placement change after the check that approved it, which is the same class of mistake as
editing a Frequency Window that allocations already reference.
"""

from __future__ import annotations

import dataclasses
import itertools


class EmptyRangeError(ValueError):
    """Raised when a range would have no width.

    Rejected rather than represented. An empty interval overlaps nothing, so it would pass
    every containment and collision check ever applied to it — a placement that reserves no
    spectrum and silently conflicts with nothing at all.
    """


@dataclasses.dataclass(frozen=True, order=True)
class FrequencyRange:
    """The half-open interval ``[start_hz, end_hz)``, in whole Hz.

    Ordered by ``(start_hz, end_hz)`` so a list of ranges sorts into spectrum order, which
    is what the gap engine in S9 will need.
    """

    start_hz: int
    end_hz: int

    def __post_init__(self) -> None:
        if not isinstance(self.start_hz, int) or not isinstance(self.end_hz, int):
            raise TypeError(
                "Frequency range edges must be whole Hz as int. A float edge is how "
                "binary floating point re-enters the engineering path (section 14.1)."
            )
        if self.start_hz >= self.end_hz:
            raise EmptyRangeError(
                f"[{self.start_hz}, {self.end_hz}) has no width. A range must contain at "
                f"least one Hz."
            )

    @property
    def width_hz(self) -> int:
        return self.end_hz - self.start_hz

    @property
    def centre_hz(self) -> int:
        """The midpoint, rounded down for an odd width.

        Lossy for an odd width, and therefore *not* the value used to rebuild a range —
        :func:`calculations.bandwidth.occupied_range` builds from the requested centre and
        bandwidth instead. This is for display.
        """
        return self.start_hz + self.width_hz // 2

    def __str__(self) -> str:
        return f"[{self.start_hz}, {self.end_hz})"

    # -- relationships ------------------------------------------------------
    def contains_hz(self, frequency_hz: int) -> bool:
        """Is this exact frequency inside the interval? The upper edge is not."""
        return self.start_hz <= frequency_hz < self.end_hz

    def contains(self, other: FrequencyRange) -> bool:
        """Does ``other`` fit entirely inside this range?

        A range ending exactly where this one ends *is* contained: both upper edges are
        exclusive, so they describe the same boundary.
        """
        return self.start_hz <= other.start_hz and other.end_hz <= self.end_hz

    def overlaps(self, other: FrequencyRange) -> bool:
        """Do the two intervals share at least one Hz?

        The same comparison PostgreSQL's ``&&`` performs on ``int8range``. It is
        reimplemented here rather than deferred to the database because the interface must
        be able to warn before a write is attempted (§8.3 keeps the database as the
        authority; this is the earlier of the two checks, not a replacement for it).
        """
        return self.start_hz < other.end_hz and other.start_hz < self.end_hz

    def is_adjacent_to(self, other: FrequencyRange) -> bool:
        """Do the two meet exactly, with no gap and no overlap? Legal by §25."""
        return self.end_hz == other.start_hz or other.end_hz == self.start_hz

    def gap_to(self, other: FrequencyRange) -> int:
        """Hz of unused spectrum between the two ranges; 0 if they touch or overlap."""
        if self.overlaps(other):
            return 0
        return max(other.start_hz - self.end_hz, self.start_hz - other.end_hz, 0)

    # -- transformations ----------------------------------------------------
    def shift(self, delta_hz: int) -> FrequencyRange:
        """Translate by a constant. ``[a, b)`` becomes ``[a+d, b+d)``; width is preserved.

        This is a non-inverting payload translation or equipment conversion.
        """
        return FrequencyRange(self.start_hz + delta_hz, self.end_hz + delta_hz)

    def reflect(self, constant_hz: int) -> FrequencyRange:
        """Reflect through ``f(x) = constant - x``. An **inverting** translation.

        The mathematics and the convention disagree here, and **A-10** resolves it. The
        exact image of ``[a, b)`` under ``f`` is the interval ``(K-b, K-a]`` — open at the
        bottom, closed at the top, because reflection swaps which edge is which.

        Every interval in this platform is half-open the other way, so the image is
        re-normalised to ``[K-b, K-a)``. **Width is preserved exactly**; what moves is
        which single Hz sits on the boundary, and it moves by one representable unit.

        That 1 Hz is a real consequence, not a rounding artefact, and it is why an
        inverting path's edges are recomputed rather than mirrored by hand.
        """
        return FrequencyRange(constant_hz - self.end_hz, constant_hz - self.start_hz)

    def expanded(self, *, left_hz: int = 0, right_hz: int = 0) -> FrequencyRange:
        """Widen by a guard on each side. Negative widths are refused."""
        if left_hz < 0 or right_hz < 0:
            raise ValueError(
                f"A guard cannot be negative (left={left_hz}, right={right_hz}). "
                f"Narrowing an allocation below its occupied bandwidth would reserve less "
                f"spectrum than the transmission uses."
            )
        return FrequencyRange(self.start_hz - left_hz, self.end_hz + right_hz)


def sort_by_start(ranges: list[FrequencyRange]) -> list[FrequencyRange]:
    """Spectrum order. ``FrequencyRange`` is ordered, so this is just a named ``sorted``."""
    return sorted(ranges)


def any_overlap(ranges: list[FrequencyRange]) -> tuple[FrequencyRange, FrequencyRange] | None:
    """The first overlapping pair in spectrum order, or ``None``.

    Sweeps rather than comparing every pair: sorted by start, an overlap can only be with
    the range holding the highest end seen so far, so one pass suffices. The naive O(n²)
    version is fine for a handful of ranges and is not fine for a Beam's worth of them.
    """
    ordered = sort_by_start(ranges)
    for earlier, later in itertools.pairwise(ordered):
        if earlier.overlaps(later):
            return earlier, later
    return None
