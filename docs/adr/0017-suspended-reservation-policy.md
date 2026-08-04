# ADR-0017 — A suspended allocation's spectrum is a setting, not a rule

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S9 — Reservations, the exclusion constraint and the gap engine
**Specification:** §15.2, §15.3
**Open question:** **OQ-08**
**Assumptions:** **A-12**

## Context

§15.2 gives an allocation eight statuses. Seven of them have an obvious answer to "does this
hold its spectrum": `PLANNED`, `PENDING_APPROVAL` and `ON_AIR` do; `DRAFT`, `CANCELLED`,
`RETIRED` and `IMPORT_REVIEW` do not.

`SUSPENDED` does not. §15.3 raises it and recommends retention without settling it, and it is
recorded as **OQ-08**. Both answers are defensible: retaining means a suspension can always be
resumed; releasing means suspended capacity is usable by somebody else.

This collides with a database fact. The exclusion constraint's partial index predicate must be
`IMMUTABLE`, so it cannot call a function that reads a setting.

## Decision

**`reserves_spectrum` is a stored boolean, written by the service** (**A-12**), and the
constraint reads the column rather than deriving it from `status`.

**A CHECK pins the seven statuses whose policy is fixed**, and deliberately says nothing about
`SUSPENDED`:

```sql
(status IN ('PLANNED','PENDING_APPROVAL','ON_AIR')          AND reserves_spectrum)
OR (status IN ('DRAFT','CANCELLED','RETIRED','IMPORT_REVIEW') AND NOT reserves_spectrum)
OR status = 'SUSPENDED'
OR status IS NULL
```

An `ON_AIR` row claiming not to hold spectrum is impossible. A `SUSPENDED` row may go either
way, and which way is decided by one setting read in one place —
`spectrum.services.reserves_spectrum` — so the service that writes the column and the screen
that explains it cannot disagree.

**Default: retain.** §15.3 recommends it, and it is the safer error. Releasing means a
suspension can silently become unresumable when somebody else takes the gap; retaining means
capacity sits idle, which is visible and reversible.

## Consequences

**What this buys.** The open question stays open in the schema rather than being closed by
implication. Answering OQ-08 as "retain" is then a no-op, and answering it as "release" is a
setting change plus a tightened CHECK — a small migration on a table whose other rows are
unaffected.

**What it costs.**

*The column can drift from the setting.* Rows written under "retain" keep
`reserves_spectrum = true` after the setting flips; nothing recomputes them. That is
deliberate — a background job silently releasing spectrum for hundreds of live allocations is
not something a settings toggle should do — but it means changing the policy requires a
deliberate, audited re-evaluation. That job does not exist yet and is noted here rather than
implied.

*One CHECK carries a gap on purpose.* A reader who does not know about OQ-08 sees an
incomplete constraint. The clause `OR status = 'SUSPENDED'` and the comment above it exist to
say the gap is the point.

**What it forecloses.** Per-allocation suspension policy. The setting is global; an allocation
suspended "but keep the spectrum" alongside one suspended "and release it" would need a second
column, and nothing in §15 asks for that.

## Alternatives considered

**Derive the predicate from `status` in the constraint.** Rejected — impossible, not merely
unwise: a partial index predicate must be `IMMUTABLE` and a settings lookup is not.

**Pick an answer now and pin all eight statuses.** Rejected. It is exactly the failure §26.20
forbids: a plausible default, indistinguishable from a confirmed one once it is in the schema,
and expensive to reverse because reversing it means altering a constraint-bearing table.

**Model suspension as a period gap instead of a status.** Rejected. Suspension is not known to
be time-bounded when it happens, and closing the active period would make resumption a new
reservation — losing the identity §18's trail follows.
