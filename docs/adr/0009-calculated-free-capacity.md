# ADR-0009 — Free capacity is calculated, never stored

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S9 — Reservations, the exclusion constraint and the gap engine
**Specification:** §16, §9.3, §26.15
**Assumptions:** **A-11**, **A-24**

## Context

§16 requires the platform to answer: what spectrum is free, where are the gaps, how wide is
the largest, what is the utilisation. Those answers drive the spectrum map, Auto-place, and
the dashboard.

The tempting shape is a `free_capacity` table maintained as reservations change. It makes the
dashboard a single query.

## Decision

**Nothing is stored. Free capacity is computed from the reservations on every read**, by a
pure function in `calculations.gaps` that takes ranges and returns ranges.

A cache of free capacity is a second source of truth for the one fact the product exists to
be right about. It can disagree with the reservations — after a rollback, after an importer
writes rows by a path the cache does not know about, after a `SUSPENDED` policy change
re-evaluates `reserves_spectrum` on rows nobody touched — and when it disagrees, nothing says
which is right. The failure is silent and it points the wrong way: a stale cache reports
spectrum as free, an operator allocates it, and the constraint refuses a placement the
platform had just offered.

**Bounds are the Beam's active assignments, not its Frequency Window** (ADR-0019), because the
OQ-27 answer says so directly: *"only within the active Beam assignments and not across the
complete Payload Path Window."* A window may be shared. Computing gaps across it would offer
an operator spectrum belonging to another Beam, and the offer would look authoritative.

**Occupancy comes from the resource, not from the assignment.** Competition is judged on the
Spectrum Resource (ADR-0018), so everything holding spectrum on the resources a leg occupies
counts against it — including allocations belonging to entirely different Beams. That is the
OQ-25 answer showing up on a screen.

**Gaps are found within each entitlement and never merged across two.** Two adjacent
assignments are two separate grants; a transmission spanning the join would sit partly outside
whichever expires first, and the platform would have proposed it.

**Auto-place is first-fit, lowest gap.** Deliberately the dullest strategy available: it is
deterministic, explainable in one sentence, and packs from one end rather than fragmenting the
middle. §9.3 requires Auto-place to propose and never save, so predictability matters more
than optimality — an operator who reopens the wizard must not be shown a different answer.

## Consequences

**What this buys.** One source of truth. Free capacity cannot be stale, because it does not
exist between reads. The arithmetic is pure and property-tested without a database:
`tests/domain/test_gaps.py` asserts, over generated inputs, that no reported gap overlaps
anything occupied, that none escapes its entitlement, and that none is empty — the three ways
a gap engine can be wrong in a way that reaches an operator.

**What it costs.**

*Every capacity read is a query plus arithmetic.* Mitigated by the partial index on
`(assignment, allocated_start_hz) WHERE reserves_spectrum` and by the fact that a Beam's
reservations are a small set. If S13's dashboard needs an aggregate across many Beams, the
answer is a materialised view refreshed on a schedule and **labelled with its refresh time** —
not a cache the code treats as authoritative.

*The utilisation figure is a float*, the only one in the engineering path. It is a display
value and nothing is derived from it; every value that *is* derived stays in integer Hz
(**A-08**).

**What it forecloses.** Historical capacity trends without a deliberate snapshot mechanism.
"What was utilisation last March" is not answerable by recomputation alone, because it depends
on assignments and reservations as they were. That is a reporting feature with its own
storage, and keeping it separate from the live answer is the point.

## Alternatives considered

**A `free_capacity` table maintained by triggers.** Rejected, as above. Triggers would keep it
closer to correct than application code would, and "closer to correct" is the wrong standard
for the platform's central claim.

**Computing gaps in SQL with range aggregates.** Rejected. PostgreSQL can do it, and the logic
would then be untestable without a database and unavailable to the wizard's live preview,
which computes against a proposal that has not been written.

**Reporting gaps across the whole Frequency Window and marking the parts outside the
assignment.** Rejected: it inverts the default. A screen that shows 200 MHz free with 180 MHz
of it flagged is one misread away from an operator trying to allocate somebody else's
spectrum.
