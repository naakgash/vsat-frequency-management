# Answer — OQ-32

**Received:** 2026-08-04
**Question:** May a Satnet Path's validity period extend beyond its Satnet's or Beam's
effective period?
**Status:** Accepted. Recorded as **A-25** in `docs/design/00-assumptions-and-open-questions.md`
and implemented per ADR-0020.

Transcribed as received.

---

## OQ-32 — Satnet Path validity containment

> All three validity-containment rules shall be hard requirements for an active or otherwise
> operational Satnet Path.
>
> A Satnet Path's active period must be fully contained within:
>
> 1. its Satnet's validity period;
> 2. its Beam's validity period; and
> 3. the validity period of the Beam Spectrum Assignment referenced by the Path.
>
> The maximum permitted period of a Satnet Path is therefore the intersection of those three
> periods. The service shall reject an operational Path whose requested period extends beyond
> that intersection. It shall identify the limiting Satnet, Beam or Spectrum Assignment and
> return the maximum valid period.
>
> For future software-defined payloads, a logical Satnet Path may remain in existence while
> its Beam Spectrum Assignment changes. The operational configuration shall therefore be
> represented by time-bounded Satnet Path revisions or activations. Each revision must
> reference one Spectrum Assignment and remain fully inside that assignment's validity period.
> A new revision is required when the assignment, Beam or relevant payload configuration
> changes.
>
> Draft records may temporarily exist outside one or more parent validity periods, but they
> shall produce warnings only and must not reserve spectrum, be provisioned or become
> operational. All three containment checks become mandatory before the record can enter an
> active state.
>
> The referenced Spectrum Assignment must also belong to the same Beam and be compatible with
> the Satnet Path's direction, polarization and Payload Path. Temporal containment alone is
> not sufficient.

---

## What this exposed

The answer requires containment within *"its Beam's validity period"*. **The Beam had no
validity period.** It carried `is_active` and an activation record and nothing else temporal.

`docs/design/02` §1 lists Beam among the `EffectiveDated` entities and `docs/design/04` §4
names a `ck_beam_effective` constraint, so the design had always expected one — S8 simply did
not build it, and nothing until now needed it badly enough to notice.

This is the second time an RF answer has found a gap rather than merely filling one. OQ-26 ruled
out a foreign key the platform would have added; this one required a column the platform had
quietly skipped.
