# ADR-0012 — Master data is superseded, not overwritten

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S5 — Dependent Inventory and Master-Data Versioning
**Specification:** §13.5, §13.6, §13.7, §15.5, §20
**Assumption:** **A-16**

## Context

§13.6 states the rule for Frequency Windows directly: *"A Window in operational use is
changed through versioning, not retroactive overwrite."*

The reason is specific rather than general good practice. From S11 a Satnet Path is
validated against a Frequency Window — it must fit inside the window, respect its edge
guard, and translate through a Payload Path's stated method and constant. That validation
happens once, at allocation. If the window's edges could later be edited in place, every
allocation made under the old numbers would still exist, still claim to be valid, and
nothing anywhere would record what it was actually validated against. The allocation would
not become wrong loudly; it would become wrong silently, which is worse.

The specification names only Frequency Windows. **A-16** extends the rule to Payload Paths
and Equipment Profiles, because the same argument holds verbatim: an allocation depends on
a path's translation constant and on a profile's LO exactly as much as on a window's edges,
and a rule that protects one of the three and not the other two protects nothing.

So the question this ADR settles is not *whether* to version — the specification says to —
but what a version **is**, and what stops two of them applying at once.

## Decision

**A version is a row.** Each version of a logical record is a separate row with its own
UUID primary key. Versions of the same logical record share a `version_group` UUID and
carry an increasing `version_number`; `supersedes` points at the predecessor.

An operational record referencing `frequency_window.id` therefore references *one specific
version*, permanently. History stays exact with no extra machinery: the row a Satnet Path
was validated against is the row it points at, and that row never changes.

**Non-overlap is a database constraint, not a convention.**

```
ExclusionConstraint(
    name="excl_window_version_overlap",
    expressions=[("version_group", RangeOperators.EQUAL),
                 ("effective_period", RangeOperators.OVERLAPS)],
    condition=Q(is_active=True),
)
```

`effective_period` is a **stored generated column** — `tstzrange(effective_from,
effective_until, '[)')` — not a value the application maintains. A column the application
computes can drift from the columns it derives from, and this one is the thing the
constraint indexes.

The half-open bound `'[)'` is what makes a handover exact: the successor starts at the
instant the predecessor ends, and the two provably do not overlap (**A-10**).

**Superseding closes before it opens.** `inventory.versioning.supersede` runs in one
transaction and, inside it, closes the predecessor's period *first*, then inserts the
successor. The order is load-bearing: an exclusion constraint is checked per statement, so
inserting first would momentarily leave two active versions of the same group and the
insert would be rejected. This is the same close-then-open discipline the reservation
engine needs in S12 (**A-14**).

**Not every field is protected.** Editing a version in operational use is refused only for
the fields that change what an allocation was validated against —
`inventory.versioning.ENGINEERING_FIELDS`, which for a window is its satellite, band, side,
polarization, edges and edge guard. A name, a description or a reference document may be
corrected in place at any time.

That line is drawn deliberately. Refusing to let someone fix a typo would push the
correction into the database, where nothing records it — a worse outcome than the one the
rule protects against.

**"In operational use" is asked, not hard-coded.** `versioning` does not know what a Beam
or a Satnet Path is; those modules sit above `inventory` and land in later slices. Usage is
read through `inventory.dependencies`, the same registry that produces the §3.2 dependency
summaries, which each module populates from its own `AppConfig.ready()`. When Beams land in
S8, windows referenced by a Beam become frozen without this module changing.

**Versioning and optimistic locking are different mechanisms.** `record_version` (§15.5)
detects two people editing the same row at once and is per-row. Versioning records that the
engineering definition changed over time and is per-logical-record. Both exist; neither
substitutes for the other.

## Consequences

**What this buys.** An allocation's definition is immutable for as long as the allocation
exists. The audit trail of a supersede carries the full before and after. The version in
force at any instant is a single indexed lookup, and the database — not application care —
is what guarantees there is only one.

**What it costs.**

Every query for "the current window" needs `effective_until IS NULL` (or a period
containment test for a historical view). Forgetting it returns every version. The list
screens default to current-only and offer *Show all versions* explicitly; the risk is real
enough that it should be a manager method rather than a filter clause once a third caller
appears.

Counting is now ambiguous. Three versions of one window are one window, and the inventory
index counts `DISTINCT version_group` for that reason. Any future count of a versioned
entity has to make the same choice consciously.

The version chain grows without bound. Nothing prunes it, deliberately — a superseded
version is exactly what an old allocation refers to, so it can never be deleted while that
allocation exists. Archival is a §22 retention question, not a modelling one.

`supersede` copies every concrete field from predecessor to successor. A field added later
is carried over automatically, which is right, but a field that should *not* be inherited
would need an explicit exclusion in `_copy_for_next_version`.

**What it forecloses.** Retroactive correction of a genuinely wrong engineering value. If a
window was entered with the wrong edges and allocations were made against it, there is no
supported route to make those allocations retrospectively correct — and there should not
be. The supported route is a new version plus a decision about the affected allocations,
which is an operational judgement, not a database operation.

## Alternatives considered

**A separate history table populated by a trigger** (`frequency_window_history`) —
rejected. It records what a row *used to* look like, which is not the same as letting an
allocation *point at* the definition it used. A Satnet Path referencing
`frequency_window.id` would still be referencing a row whose values had changed underneath
it; the history table would tell you it changed, after the fact, without preventing
anything.

**A `valid_from`/`valid_to` pair with no generated column** — rejected. The exclusion
constraint needs a range to index. Building it inline in the constraint expression works
but is re-evaluated per comparison and cannot be indexed, and an application-maintained
range column can drift from its own endpoints.

**`django-simple-history` or a similar package** — rejected. These model *audit* history,
not *effective-dated* master data: they answer "what did this row look like on Tuesday",
not "which definition does this allocation depend on". The distinction is the whole point,
and the append-only audit trail (ADR-0013) already covers the first question.

**Versioning every inventory entity** — rejected. A Gateway's coordinates or a Hub's vendor
are not inputs to a spectrum calculation; versioning them would impose a changeover date on
routine corrections and teach people that superseding is a formality. The three versioned
entities are exactly the three whose values a calculation consumes.

**Refusing all edits to an in-use version** — rejected, as described above: it makes
correcting a typo impossible through the application, which is a reliable way to get people
editing the database by hand.
