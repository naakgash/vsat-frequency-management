# ADR-0016 — Guard policies resolve through a fixed hierarchy

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S6 — Calculation Engine
**Specification:** §9.2, §13.6, §13.9, §25
**Open question:** **OQ-07** — the values themselves

## Context

A guard band is not one number with one source. The specification puts a default in three
places:

- §13.6 gives a **Frequency Window** a default guard policy;
- §13.9 gives a **Satnet** one;
- §9.2 lets an operator *"select or accept"* a policy on the path itself.

Nothing in the specification says which wins. That has to be decided somewhere, and it has
to be decided *once*: if two screens resolve it differently they will show two different
allocated bandwidths for one placement, and neither will look wrong on its own.

The stakes are higher than presentation. §25 makes adjacency legal, so the guard is the
*only* thing expressing required separation — an accidentally-zero guard does not produce
an error, it produces a placement that is allowed to sit flush against its neighbour.

## Decision

**One order, most specific first:**

```text
1. explicit per-path override      entered by an authorised user
2. Satnet.default_guard_policy
3. FrequencyWindow.default_guard_policy   (of the canonical-side window)
4. system default guard policy setting
```

Implemented as `calculations.guards.resolve_hierarchy(*candidates, occupied_bandwidth_hz=…)`.
Variadic rather than four named parameters: the caller states the order, and adding a level
later does not change the signature of the function that respects it.

**The resolved source travels with the widths.** `GuardWidths` carries a `GuardSource` —
`OVERRIDE`, `SATNET`, `WINDOW`, `SYSTEM` or `NONE` — and the policy's label. §9.2 lets an
operator *accept* a policy, and accepting one whose origin you cannot see is not a decision.
A reviewer needs the same thing: an explicit override and an inherited default look
identical once they are just two numbers.

**Both the policy and the resolved Hz are stored on the Satnet Path** when one is created in
S11. Editing a policy afterwards cannot retroactively change an allocation that was
validated against the old widths — the same rule as ADR-0012, for the same reason.

**Nothing configured means zero, and zero is reported.** `NO_GUARD` is
`left_hz=0, right_hz=0, source=NONE`, and `validation.check_placement` raises a
`NO_GUARD_APPLIED` **warning** whenever the total is zero.

Zero is the honest answer to "no policy exists". Inventing a plausible 250 kHz would be
indistinguishable from a confirmed value once it reached an allocation, which is precisely
what §26.20 forbids. But an unguarded placement is far more often a missing policy than a
deliberate choice, so the absence is surfaced rather than assumed. It warns rather than
blocks because adjacency *is* legal (§25) and blocking would make the platform unusable
while **OQ-07** is open.

**Three modes, and the values are OQ-07.** `FIXED`, `PERCENT_OF_OCCUPIED`,
`MAX_OF_FIXED_AND_PERCENT`. The third exists because a band whose transmissions span three
orders of magnitude in bandwidth needs both a floor and a proportion — "at least 100 kHz,
and at least 5%". No policy is seeded, in the engine or in the database.

**Percentages are `Decimal`, and guards round up** (**A-09**). A guard is a *minimum*
separation, so a fractional Hz becomes a wider gap rather than a narrower one. `0.1` as a
float is not one tenth, and a guard derived from it would put an approximate edge on an
interval the database compares exactly.

## Consequences

**What this buys.** One resolution order, in one function, used by the wizard, the importer
and Auto-place alike. An operator can see which policy applied and why. A policy edit cannot
silently re-scope existing allocations.

**What it costs.**

Four levels is more than most cases need — the common one is a Window default and nothing
else. The cost is a lookup that usually returns at level 3, which is negligible, against a
model that does not need extending the first time someone wants a per-Satnet exception.

Storing the resolved widths means a policy correction does not propagate. Applying it to
existing paths is a deliberate operation someone has to perform and review, not a side
effect of an edit. That is the intended trade and it is the same one ADR-0012 makes.

`MAX_OF_FIXED_AND_PERCENT` computes both components on every call. Irrelevant at this scale,
and it keeps the mode honest: the alternative is a short-circuit that makes the result depend
on evaluation order.

**What it forecloses.** A guard that varies with something other than the occupied bandwidth
— distance to the window edge, say, or the neighbour's modulation. If that is ever needed it
is a new mode, not a change to the hierarchy.

## Alternatives considered

**A single system-wide guard** — rejected. §13.6 and §13.9 both carry a default, so the
specification already assumes more than one, and a single value cannot serve a Ka-band
trunk and an L-band telemetry link.

**Additive resolution — sum every applicable policy** — rejected. It makes an explicit
override unable to *narrow* a guard, which is the main reason someone sets one, and it makes
the total depend on how many levels happen to be configured.

**Widest-wins instead of most-specific-wins** — rejected. It is safe in the sense that it
never under-guards, and it would mean an operator with a legitimate reason to reduce a guard
could not, without an administrator editing shared master data. That converts a per-path
decision into a change that affects every other path.

**Computing the guard at read time from the current policy** — rejected. Cheap, and it means
an allocation's reserved width changes when someone edits a policy — including allocations
that are on air. See ADR-0012.
