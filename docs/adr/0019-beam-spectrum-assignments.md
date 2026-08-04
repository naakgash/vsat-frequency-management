# ADR-0019 — A Beam's usable spectrum is its active assignments, not its Window

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S9a — Spectrum Resources and Beam Spectrum Assignments
**Specification:** §5.2, §5.3, §13.6, §16
**Answers:** **OQ-27**
**Revises:** **A-06** (window identity)
**Assumptions:** **A-24**

## Context

Up to S8 a Beam direction had to use **the whole of** its Payload Path's Frequency Windows.
Identity was enforced, the wizard did not offer the windows as fields, and a Beam whose
windows differed was refused with a message citing **OQ-27**. That was a deliberate holding
position: identity is the restrictive reading, so answering it later would arrive as a
feature rather than as a silent behaviour change.

The answer widens it:

> A Beam may use one or more sub-ranges of its Payload Path Frequency Window. The Payload
> Path Frequency Window represents the **maximum payload capability**. The spectrum
> operationally assigned to a Beam shall be represented by separate, **time-bounded** Beam
> Spectrum Assignment records associated with a payload-configuration version.

And it states the consequence for free capacity explicitly:

> The free-capacity engine shall calculate available capacity **only within the active Beam
> assignments** and not across the complete Payload Path Window.

## Decision

**A `BeamSpectrumAssignment` is a half-open RF sub-range of one of a direction's Frequency
Windows, with its own half-open effective period.** A direction may hold one or more per
window. The window becomes a ceiling; the assignments are the spectrum.

**Containment is a database CHECK, not a service rule.** The assignment denormalises its
window's edges and a composite foreign key to `frequency_window (id, rf_start_hz, rf_end_hz)`
keeps the copy honest, so `assignment ⊆ window` is per-row and checkable. This is the same
device the Payload Path already uses to pin its windows' sides, and it is used here for the
same reason: the alternative is a service check that a direct SQL update walks straight past.

**Two active assignments on one window may not overlap in RF and time at once.** An
exclusion constraint on `(direction_config =, frequency_window =, rf_range &&,
effective_period &&) WHERE is_active`. Overlapping assignments would leave two answers to
"what is this Beam allowed to use", and the gap engine would count the shared spectrum twice.

**Assignments are pinned to the Payload Path version they were drawn against.** The answer
requires association with a payload-configuration version, and `PayloadPath` is already the
versioned record of a payload configuration (**A-16**). Superseding the path does not
silently re-point an assignment at a different payload.

**The fixed-HTS case is the degenerate one, and it is created automatically.** Configuring a
direction creates one assignment per window, equal to the whole window, open-ended — which
is exactly the behaviour S8 had. The answer permits this directly: *"a Beam Spectrum
Assignment may equal the complete Payload Path Window and remain continuously active."*

That last point is the one that makes this change safe to land before the reservation engine
exists. Today's Beams keep working, the screens show a full-width assignment instead of an
implicit one, and the model is ready for a payload whose routing moves.

## Consequences

**What this buys.** Sub-ranges of a shared transponder, expressible without a schema change.
A free-capacity engine that reports a Beam's own spectrum rather than the transponder's. And
a time dimension on assignment, so a payload reconfiguration is a new assignment rather than
an edit that rewrites history.

**What it costs.**

*Every containment check moves.* "Inside the window" becomes "inside an active assignment",
in frequency **and** in time. Two-dimensional containment is easy to half-implement: checking
RF and forgetting the period gives an allocation that is valid today and silently outside its
assignment next month. The service resolves both together and returns the assignment it
matched, so nothing downstream re-derives it.

*A Satnet Path's validity period is now bounded by its assignment's.* Whether that is a hard
constraint or a warning is **OQ-32**, which is now more consequential than when it was
recorded, because there is a second period to sit inside.

*The gap engine gets harder.* Free capacity is the union of active assignments minus
reservations, not one window minus reservations. With one full-width assignment the answer is
identical, which is a good place to start and a bad place to stop testing — the tests
therefore cover the multi-assignment case even though no live payload uses it yet.

*A direction with no active assignment can allocate nothing.* Correct, and it will be
surprising the first time it happens after an assignment expires. The validation reports it
as a finding with the expiry date rather than as an empty gap list.

**What it forecloses.** Assignments crossing window boundaries. An assignment belongs to
exactly one window, so spectrum spanning two windows is two assignments — which is right:
the windows are separate grants of permission and may be superseded independently.

## Alternatives considered

**A nullable sub-range pair on `BeamDirectionConfig`** — `sub_range_start_hz` /
`sub_range_end_hz`, null meaning the whole window. Rejected: it permits exactly one
sub-range, and the answer says *"one or more"*. It also has nowhere to put the effective
period, which is the half of the answer that makes it work for software-defined payloads.

**Assignments on the Beam rather than the direction** — rejected. A window belongs to a
direction's chain, and an assignment carves a window; hanging it off the Beam would allow an
assignment against a window the Beam's other direction does not use.

**Keep identity and treat sub-ranges as a later slice** — rejected. Identity is not a subset
of the new rule; it is the special case where exactly one assignment spans the whole window.
Building the general model now costs one table, and retrofitting it later means migrating
live allocations whose containment was checked against a different bound.

**Compute the assignment from the reservations that exist** — rejected outright. That
inverts the guarantee: assignments would describe what was allocated rather than bound what
may be, and nothing would ever be refused.
