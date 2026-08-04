# ADR-0020 — A Satnet Path lives inside the intersection of three periods

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S10a — Validity containment
**Specification:** §13.9, §15.2, §9.5
**Answers:** **OQ-32**
**Assumptions:** **A-10**, **A-25**

## Context

**OQ-32** asked whether a Satnet Path's validity may extend beyond its parents'. The register's
provisional position was the loose one — *"containment enforced at service level, warned in UI,
not a DB constraint"* — and S9a made the question sharper by adding a third period: an
allocation must now also sit inside its Beam Spectrum Assignment's.

The answer is the strict reading, and it adds two things the question did not ask:

> All three validity-containment rules shall be hard requirements for an active or otherwise
> operational Satnet Path… The maximum permitted period of a Satnet Path is therefore the
> intersection of those three periods. The service shall reject an operational Path whose
> requested period extends beyond that intersection. **It shall identify the limiting Satnet,
> Beam or Spectrum Assignment and return the maximum valid period.**

and

> **The referenced Spectrum Assignment must also belong to the same Beam and be compatible with
> the Satnet Path's direction, polarization and Payload Path. Temporal containment alone is not
> sufficient.**

## What the answer exposed

**The Beam had no validity period.** It carried `is_active`, `activated_at` and `activated_by`
and nothing temporal at all.

`docs/design/02` §1 lists Beam among the `EffectiveDated` entities and `docs/design/04` §4 names
a `ck_beam_effective` constraint, so the design had expected one since the design pass. S8 did
not build it, and nothing until now needed it badly enough for the omission to surface.

This is the second RF answer to find a gap rather than fill one: OQ-26 ruled out a foreign key
the platform would have added, and OQ-32 required a column it had quietly skipped.

## Decision

**`Beam` gains `effective_from` / `effective_until`**, half-open, with `ck_beam_effective_period`.
Defaulting to *now, open-ended*, so nobody has to type a date to mean "no end planned".

**Validity and activation are separate, and both bind.** `is_active` is a switch somebody flips
now; validity is the span over which the Beam is a real thing. A Beam can be inside its validity
and switched off. Collapsing them would make deactivating a Beam retroactively invalidate
allocations that were legitimate when made.

**One module computes the answer**: `satnets.containment.evaluate` returns a `Verdict` carrying

- `permitted` — the intersection, or `None` when the three share no common period;
- `limiter` — which parent bounded it, by name;
- `findings` — what is wrong, with a message naming the cause and the maximum.

`None` rather than an empty period for the no-overlap case, deliberately: *"there is no moment
when all three are valid"* is a different statement from *"the window is zero wide"*, and only
the first is true when a Beam expires before its Satnet begins. A caller handed an empty period
would offer it as a maximum.

**Draft and operational are the same rules and different verdicts.** The answer permits a draft
outside its parents' periods, as warnings. `evaluate` takes an `operational` flag that decides
*severity* and never *rules* — two code paths would eventually disagree about what "contained"
means, and the definition that matters is the one applied at activation.

**Compatibility is checked in the same place as containment.** The assignment must be the Beam's
own, on the Path's direction, at a window of the Path's polarization, and drawn against the
payload path the direction currently uses. A module called "containment" that let a Path point
at another Beam's assignment would be the worst possible place to keep half a rule — every date
correct, and the spectrum somebody else's.

**The interval arithmetic is pure.** `calculations.periods` is Django-free and property-tested,
the time-axis twin of `calculations.ranges`. The open-ended upper bound is modelled as genuine
infinity in one place: every wrong way to handle `None` fails quietly, and the asymmetry that
matters — *a bounded parent does not contain an open-ended child* — is one line there instead of
a condition every caller has to remember.

## Consequences

**What this buys.** A refusal an operator can act on: the limiting parent named, and a date that
would be accepted. Each of the three parents belongs to somebody different — a Satnet's dates to
the operator, a Beam's to engineering, an assignment's to the payload plan — so "outside the
permitted period" without a cause is three conversations instead of one.

**What it costs.**

*Containment is not a database constraint, and cannot be.* It spans four tables, and a CHECK is
per-row. `spectrum.services` still enforces the assignment period on the reservation itself,
which is the last line, but the three-way rule is service-level. That is a real gap relative to
the rest of the product's "the database is the final authority" posture, and it is stated here
rather than left to be discovered: a direct SQL insert can create a Path outside its Satnet's
period.

*Every Beam now needs dates.* Existing Beams default to valid-from-creation, open-ended, which
preserves current behaviour exactly — and means the column is doing nothing until somebody sets
an end date, at which point it starts refusing allocations that used to be accepted.

**What it forecloses.** A Satnet Path spanning two spectrum assignments. The answer requires one
assignment per revision, and a period straddling a handover is outside whichever assignment it
leaves. S12's revision model is where that becomes a workflow rather than a refusal.

## Alternatives considered

**Three independent checks returning three booleans.** Rejected: it cannot produce the maximum
permitted period, which is the part of the answer that makes the refusal useful.

**A generated `tstzrange` on the Path plus an exclusion constraint against each parent.**
Rejected — PostgreSQL exclusion constraints operate within one table, and a containment rule
across four is not expressible without triggers, which would put the rule somewhere no reader of
the model would find it.

**Evaluating drafts with a separate, looser function.** Rejected. The two would drift, and the
one that drifts unnoticed is the strict one, because drafts are what people exercise daily.

**Deriving the Beam's validity from its activation record.** Rejected: `activated_at` is when a
switch was flipped, not a statement about the span over which the Beam exists, and a Beam
deactivated and reactivated would have a validity with a hole in it that nothing intended.
