# ADR-0014 — An on-air allocation is closed and replaced, never overwritten

**Status:** Accepted
**Date:** 2026-08-05
**Slice:** S12 — Lifecycle, approvals and revisions
**Specification:** §15.2, §15.4, §15.5, §15.6
**Assumptions:** **A-12**, **A-14**
**Open questions:** **OQ-08**, **OQ-11**

## Context

§15.4 says an `ON_AIR` record is changed by revision rather than by edit. The reason is not
bookkeeping: an allocation that somebody approved and that a modem is transmitting on is a
statement about the real world at a moment in time, and rewriting it in place destroys the only
record of what was actually on air last week.

Two things make it harder than copying a row.

**The spectrum has to move with the status.** `PLANNED`, `PENDING_APPROVAL` and `ON_AIR` hold
their spectrum; `DRAFT`, `CANCELLED` and `RETIRED` do not (**A-12**). So a revision is not one
write — it is a release and a reserve, and the exclusion constraint is watching both.

**The successor usually overlaps its predecessor.** Most revisions change a bandwidth or a
period and keep the frequency. The predecessor holds that frequency until something releases
it, and the constraint is `IMMEDIATE` (**A-14**) — so the order of statements decides whether
the operation is possible at all.

## Decision

**One transaction, and the order is the design.** `lifecycle.revise`:

```text
1. old.valid_until := change_effective_at        # closes the period
2. old.status      := RETIRED                    # ... which means it holds nothing
3. release(old)                                  # the occupancy rows go
4. new := recompute(inputs, valid_from=change_effective_at, revision_number+1, supersedes=old)
5. reserve(new)
```

Steps 1–3 must precede step 5. Reverse them and a revision that keeps its own frequency is
refused by the row it is replacing — an error that looks like a spectrum conflict and is
actually a bug in the write order. A test creates exactly that revision to hold the ordering in
place.

**The successor is recomputed, not copied.** Only the operator's inputs carry forward — mode,
value, roll-off, guard policy, centre, hardware references. Every derived edge is computed
again from whatever master data is current. Copying the stored edges would carry a superseded
Payload Path's arithmetic into an allocation validated against the current one, which is the
opposite of what versioning is for (**A-16**).

**Approval is not inherited.** A revision of an `ON_AIR` or `PENDING_APPROVAL` record enters at
`PENDING_APPROVAL`; a revision of a `PLANNED` one enters at `PLANNED`. An operator who could
revise an approved allocation into a new frequency without a second decision would have found a
way around §12 by pressing "revise" instead of "edit".

**The chain is a group, not a walk.** `revision_group` is constant across every revision, so the
history view is one indexed query rather than a recursive walk up `supersedes`. `ck_path_revision`
already refused a chain that loses its head.

## The transition graph is data

`TRANSITIONS` is a dictionary keyed by the status being left, holding the action, the
destination, the capability it needs and what it does to the spectrum. Everything else reads
it: legality, the buttons a screen offers, and the capability a view declares.

A graph expressed as `if` statements across views is one that disagrees with itself the first
time somebody adds a status — and §15.2's is exactly the kind of graph that gets an extra state.
Adding one now means adding a row.

## Two open questions stay open, as settings

**OQ-08** (does a suspension hold its spectrum?) and **OQ-11** (must the approver be a second
person?) are both **positions rather than rules**, and both are implemented as settings with the
defaults the specification recommends: retain, and yes. Tests run the suspension both ways,
which is the point of ADR-0017's stored `reserves_spectrum` column.

They are **Django settings, not the `SystemSetting` table** `docs/design/02` §8 sketches. The
readers sit below `operations` in the module layering, so a database-backed store there would be
unreachable from the code that needs it without inverting the dependency. When a settings screen
arrives it can back these two names with a table; nothing above them has to change.

## Consequences

**A status change can now be refused by the constraint.** Resuming a suspension that released
its spectrum under the "release" policy is the case: somebody else took the gap, and the resume
collides. It arrives as a message about somebody else's transmission rather than as an integrity
error, which is why `spectrum.services` translates on updates as well as on inserts.

**An approval cannot be reached through the transition service.** `lifecycle.transition` refuses
`approve` and `reject` unless `approvals.services` is calling, because a decision that skipped
that module would move an allocation on air and leave no `ApprovalDecision` behind it — the
trail would show a status change with nobody attached to it (§18).

**Optimistic locking reaches buttons, not only forms.** §15.5's `record_version` is carried by
every lifecycle form on the detail screen, so a button pressed on a page rendered ten minutes
ago is refused with the same difference view a stale edit gets.
