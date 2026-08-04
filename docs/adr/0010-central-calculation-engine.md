# ADR-0010 — One calculation engine, and it is pure

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S6 — Calculation Engine
**Specification:** §9.2, §9.4, §11, §14.1, §14.3
**Assumptions:** **A-08**, **A-09**

## Context

§11 is unambiguous: the backend result is authoritative, and no template or script
recalculates it. §14.3 requires *"one documented rounding policy"*.

Both requirements have the same failure mode, and it is not a wrong formula. It is **two**
formulas — a JavaScript preview that updates as the operator types, an export that
recomputes a column, an importer that derives a value on the way in. Each is written by
someone reasonable, each is nearly right, and the disagreement surfaces months later as
"the tool shows 13.500 and the spreadsheet shows 13.499".

Every formula therefore needs one home, and that home needs to be somewhere the other
candidates cannot reach.

## Decision

**One package: `calculations/`.** Occupied bandwidth, derived symbol rate, edge placement,
guard resolution and placement validation live there and nowhere else.

**The engine is pure, and it is enforced.** These modules —
`bandwidth`, `guards`, `ranges`, `rounding`, `types`, `units`, `validation` — import the
standard library and each other. Nothing else. An import-linter contract, `the calculation
engine is pure`, forbids them from importing **Django itself**, not merely Django models:

```toml
source_modules = ["calculations.bandwidth", "calculations.guards", ...]
forbidden_modules = ["django", "config", "accounts", "audit", "inventory", ...]
```

Django is on that list deliberately. Importing `django.conf.settings` would make the
arithmetic depend on configuration, and a rounding policy that varies by environment is not
a policy. The contract was verified by temporarily adding a Django import and watching it
break, because a contract nobody has seen fail is a contract nobody knows is wired up.

`calculations/{forms,views,urls,apps}.py` are the Django surface *over* the engine and are
deliberately outside the contract.

**The engine consumes value types, not models.** `BandwidthRequest`, `GuardPolicySpec`,
`GuardWidths`, `FrequencyRange`, `Placement` — frozen dataclasses. The caller converts a
`GuardPolicy` row into a `GuardPolicySpec`; the engine never sees the model and so cannot
grow a dependency on a column.

**`place()` is the entry point.** Callers use it rather than assembling
`resolve_request` → `occupied_range` → `allocated_range` themselves, so the order of
operations is decided once. Three call sites assembling it by hand is how two of them end
up applying the guard before the rounding.

**Rounding is outward, in one module** (`calculations.rounding`, **A-09**). Occupied
bandwidth and guards round up; a derived symbol rate rounds down. Each errs towards the
answer that cannot cause a collision or an over-claim.

**Accidental rounding raises.** `rounding.EXACT` is a `Decimal` context that traps
`Inexact`. Arithmetic that must be exact — symbol rate × (1 + roll-off), a percentage of a
bandwidth — goes through it and *raises* rather than silently dropping digits. Division is
inherently inexact, so it uses a non-trapping context and hands the result to an explicit
`ceil_hz` or `floor_hz`. Policy rounding is visible at the call site; accidental rounding is
an exception. Keeping them apart is what stops a stray digit masquerading as the policy.

**`Decimal` at the boundary, `int` in storage.** A float roll-off is refused with a
`TypeError` rather than coerced.

## Consequences

**What this buys.** The property tests in `tests/domain/` construct thousands of placements
per run in milliseconds, with no database and no fixtures — which is why S6 could be built
and argued about before any inventory data existed. When RF engineering disputes a number,
there is one function to look at. The importer (S15) and the exporter (S14) inherit the
engine rather than reimplementing it.

**What it costs.**

A conversion layer at every call site: a `GuardPolicy` row has to become a
`GuardPolicySpec`. That is boilerplate, and it is the price of the engine not knowing what
a row is.

`GuardMode` exists twice — once as a `TextChoices` for the database, once as a `StrEnum`
for the engine — because `calculations` sits below `inventory`. Mirroring three strings is
cheap; drifting is not, so `tests/domain/test_guards.py` compares the two member-for-member.
A mode added on one side and not the other would otherwise fall through the engine's `match`
and resolve to **no guard at all**, on a policy that looks correctly configured.

No live client-side recalculation. The Engineering Preview posts and re-renders. That is
slower than a JavaScript preview and it is the point: there is no second implementation to
disagree with.

**What it forecloses.** Any future "quick calculation" in a template, an export formula or
a JS helper. Deliberately.

## Alternatives considered

**Methods on the Satnet Path model** — rejected. It reads naturally and makes the formulas
untestable without a database, unavailable to the importer before a row exists, and
reachable only through the ORM. Every property test would need a transaction.

**A service layer with no separate package** — rejected. Services authorise, transact and
audit; the engine does none of those and must not learn to. Mixing them means the
arithmetic acquires a `request` argument within two slices.

**Mirroring the formulas in JavaScript for a live preview** — rejected, and this is the one
worth naming, because it is the thing that will be asked for. It is a better interaction and
it is exactly the second implementation §11 forbids. If a live preview is needed later, the
right shape is an HTMX round trip to the same engine, not a copy of it.
