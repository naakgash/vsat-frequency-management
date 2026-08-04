# ADR-0004 — The Beam is the root pool, and each direction is a child row

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S8 — Beam and Beam Builder
**Specification:** §5.1–§5.4, §10.1, §25, §26.6
**Assumptions:** **A-01**, **A-06**, **A-07**, **A-16**

## Context

§5 makes the Beam the **root spectrum pool** with exactly two direction chains, and each
chain is the same five things: an uplink window, a payload path, a downlink window, an
equipment pool, and a polarization pair.

Two shapes present themselves, and they are not equivalent:

- **(a)** flatten both chains onto `Beam` as some twenty `fwd_*` / `rtn_*` columns;
- **(b)** a `BeamDirectionConfig` child row per direction.

The choice matters more than it looks, because of §26.6: *a Beam cannot be activated while
its mandatory FWD/RTN configuration is invalid*. Validation state is **per direction**, and
§5.4 additionally allows a direction to be **explicitly disabled** — a receive-only Beam is
a real configuration, not an omission.

Removing `Interference Domain` (§4, **A-01**) also puts weight here: the Beam becomes the
reuse key for the whole spectrum model, so its shape decides what the S9 exclusion
constraint can be keyed on.

## Decision

**(b) — a child row per direction.** One `BeamDirectionConfig` per `(beam, direction)`,
unique together, both created with the Beam.

Flattening would duplicate every validation rule and every uniqueness rule twice, and the
two copies would eventually disagree — the standard fate of parallel code. With a child row
the rules are written once and applied per row.

**Both direction rows exist from creation**, enabled and unconfigured. That is what makes
"not configured yet" and "deliberately disabled" distinguishable, which §5.4 requires: if a
direction were simply a missing row, the two would be the same absence and the interface
could not tell an administrator which one they were looking at.

**A Beam is not master-data versioned** (**A-16**). It is an operational configuration
rather than an engineering definition a calculation consumes — what a Satnet Path is
validated against is the Frequency Window and the Payload Path, both of which *are*
versioned. Optimistic locking plus the audit trail is the right weight.

**Windows are stored explicitly and must be identical to the path's** (**A-06**). §5.2 and
§5.3 list the windows *and* the payload path; the explicit foreign keys give query stability
and a clear audit record, and `beams.validation` enforces identity. Not containment —
identity. Narrowing a Beam to a sub-range of a shared transponder is **OQ-27** and is not
supported, so enforcing identity now means that answer arrives as a feature rather than as a
silent behaviour change.

The wizard does not offer the windows as fields at all. Choosing the path fixes them, and
the surest way to guarantee identity is never to accept them as input.

**Three configuration states, not a boolean.** `INCOMPLETE`, `INVALID`, `VALID`. "Not
finished" and "finished but wrong" call for different things from the person looking at
them: a half-built Beam needs the rest of the wizard, an invalid one needs a decision.
Incompleteness outranks invalidity in the precedence, because a half-configured direction
produces rule failures that are only consequences of the missing data.

**Activation re-validates rather than trusting the cached state.** `Beam.configuration_state`
is a cache so a list of fifty Beams renders fifty badges without running fifty validations.
It is not the authority: the master data underneath a Beam can be superseded between the
last run and the button press, so a stored `VALID` is evidence of what was true earlier.

**§26.6 is enforced in three places, and only two of them are guarantees:**

1. `beams.services.set_active` re-validates and raises with the failing rules attached;
2. `ck_beam_active_requires_valid_configuration` on `beam` makes the state unreachable;
3. the builder disables the button — a convenience, and explicitly labelled as one.

The rule that an *enabled* direction must be configured is deliberately **not** a database
constraint. It spans two tables — the enabled flag on the child, the activation on the
parent — and a CHECK is per-row and cannot join. Django rejects it outright as `models.E041`.
So the database enforces the *consequence* on the Beam instead, which is the stronger
placement anyway: it closes every route to the forbidden state, including a direct SQL
update, where a constraint on the child could only have caught one way of arriving there.

**Equipment is a set with priorities.** §5.2 and §5.3 say "profile **or** profile set", so
the set is modelled and a single profile is the degenerate case. This is the candidate pool
a Satnet Path picks from in S11 (**A-05**); the wizard only asks an operator to choose when
more than one candidate is still valid (§9.2).

**Validation runs are kept.** `BeamValidationResult` is append-only, like the audit trail
and for the same reason: "the Beam was valid when we activated it" is a claim unless the run
that said so still exists (§18). Findings are stored as JSON rather than rows — they are
read as a set, never queried across, and a rule added in a later slice would otherwise need
a migration to store its own result.

## Consequences

**What this buys.** One set of rules for two structurally identical chains. A disabled
direction that is visibly disabled. A per-direction validation state that a wizard can act
on. An activation that cannot happen on an invalid configuration by any route. And a Beam
shaped to be the reuse key that S9's exclusion constraint will need.

**What it costs.**

Every read of a Beam's configuration is a join. Mitigated with `select_related` and
`prefetch_related` in `beams.selectors`, and the alternative — twenty columns — has a worse
version of the same problem the first time a chain grows a field.

Two rows exist for every Beam whether or not both are used. That is the price of §5.4 being
expressible at all.

The cached `configuration_state` can be stale, by construction. It is a cache and the code
says so in three places, but a reader who trusts the badge on a list page is trusting
something that was true at the last write. The detail page and the activation path both
re-validate; a list page cannot afford to.

**What it forecloses.** A Beam with more than two directions, and a Beam whose windows are a
sub-range of its payload path's (**OQ-27**). Both are deliberate: the first is not in §5, and
the second changes containment validation and the gap engine together.

## Alternatives considered

**Flatten both chains onto the Beam** — rejected, as above. Twenty columns, every rule
written twice, and `fwd_uplink_window_id` / `rtn_uplink_window_id` diverging the first time
someone fixes a bug in one of them.

**One row per *enabled* direction, absence meaning disabled** — rejected. It makes §5.4's
"explicitly disabled" indistinguishable from "not configured yet", which is precisely the
distinction §5.4 asks the interface to show.

**Derive the windows rather than storing them** — rejected. The foreign keys are what make a
Beam's history legible after its payload path is superseded, and §5.2 lists them as part of
the configuration rather than as a lookup.

**Compute `configuration_state` on read, with no column** — rejected for the list page
alone: fifty Beams would mean fifty validation runs, each touching four tables. The column
exists for that one screen and is treated as untrusted everywhere it matters.

**Validate only the edited direction after a wizard step** — rejected. Two rules span both
directions ("at least one is enabled", and the satellite check), so a per-direction
re-validation would leave the Beam's own state stale exactly when it changed.
