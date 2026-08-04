# ADR-0008 — Half-open ranges, including through spectral inversion

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S6 — Calculation Engine
**Specification:** §8.1, §8.4, §14.5, §25
**Assumptions:** **A-10**, **A-11**

## Context

§8.4 asks for half-open ranges and §25 states that adjacency is legal — two allocations may
touch. Those two together decide more than they appear to.

The reason is §8.1: an allocated interval is exclusive to one Satnet Path, and §8.3 has
PostgreSQL enforce that with a range-overlap constraint. Overlap is decided by comparing
endpoints, so the convention at the endpoint *is* the rule. With closed intervals
``[0, 100]`` and ``[100, 200]`` share the frequency 100 and the constraint rejects them,
even though physically nothing is wrong — which would make legal adjacency impossible to
express and push people towards a fudge factor. With half-open intervals ``[0, 100)`` and
``[100, 200)`` provably do not overlap, and no tolerance is needed anywhere.

The awkward case is spectral inversion, and it is not avoidable: an inverting payload
translation or a high-side equipment conversion maps ``f(x) = K − x``, and the platform has
to know what happens to the interval.

## Decision

**Every interval is ``[start, end)``.** RF in Hz, time as ``[valid_from, valid_until)``.
One convention, no exceptions, expressed in `calculations.ranges.FrequencyRange` and in the
`int8range`/`tstzrange` columns that store the result.

**An empty range is refused, not represented.** `FrequencyRange` raises when
``start >= end``. An empty interval overlaps nothing, so it passes every containment and
collision check ever applied to it — a reservation that silently conflicts with nothing at
all is worse than one that fails loudly.

**Float edges are refused.** The constructor requires `int`. A float edge is the easiest
route back to binary floating point in the engineering path (§14.1).

**Inversion re-normalises, and loses one Hz of edge, not of width.** This is the part that
needs writing down.

The exact image of ``[a, b)`` under ``f(x) = K − x`` is ``(K−b, K−a]`` — reflection swaps
which endpoint is included. That interval is closed at the top, which no other interval in
the platform is. It is re-normalised to ``[K−b, K−a)``.

```text
[a, b)  --reflect(K)-->  (K-b, K-a]   (the exact image)
                     ==  [K-b, K-a)   (as stored)
```

**Width is preserved exactly.** What moves is which single Hz sits on the boundary, and it
moves by one representable unit. `reflect` is its own inverse, which is the property that
makes the re-normalisation consistent rather than lossy, and it is tested as such.

**Separation is always a guard, never a gap** (**A-11**). Because adjacency is legal, any
required physical separation has to be modelled explicitly as a guard band. There is no
implicit spacing anywhere, and a zero guard means zero.

## Consequences

**What this buys.** Adjacency is decidable without a tolerance, so §25 works as written.
The Python check and the PostgreSQL `&&` operator agree by construction, so the interface
can warn before a write is attempted without ever disagreeing with the database that
follows. Gap detection in S9 is arithmetic on sorted endpoints rather than a special case
per boundary.

**What it costs.**

The upper edge reads as off-by-one to anyone who has not met the convention. `29,500.000
MHz` displayed as a window end is not part of the window. The screens say so explicitly
rather than assuming it is understood, and `__str__` renders `[start, end)` with the
brackets for the same reason.

The 1 Hz that moves under inversion is real. An allocation planned on the uplink side and
one planned on the downlink side of the same inverting path will not have identical edge
arithmetic, and the difference is a single Hz at one edge. This is why S7 recomputes an
inverted side rather than mirroring it by hand — and why the round-trip property is tested
rather than assumed.

**What it forecloses.** Expressing "these two must be at least X apart" without naming X.
That is deliberate: an implicit separation is a number nobody agreed to.

## Alternatives considered

**Closed intervals with a 1 Hz gap convention** — rejected. It expresses adjacency as
``[0, 100]`` next to ``[101, 200]``, which is arithmetically fine and operationally
misleading: the 1 Hz gap is not a real guard band, it is an artefact of the representation,
and it would eventually be mistaken for one.

**A tolerance on the overlap comparison** — rejected outright. It turns a hard guarantee
into a tunable one, and the correct tolerance for a 30 GHz uplink is not the correct
tolerance for a 950 MHz IF.

**Storing the closed form for inverted sides** — rejected. Two conventions in one system
means every comparison has to know which side it is looking at. The re-normalisation costs
one Hz at one edge, once, in a documented place.
