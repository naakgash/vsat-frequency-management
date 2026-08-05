# Answers — OQ-09, OQ-10, OQ-22, OQ-23

**Received:** 2026-08-04
**Status:** Accepted. Recorded as **A-26** to **A-29** in
`docs/design/00-assumptions-and-open-questions.md` and implemented per ADR-0021.

Transcribed as received.

---

## OQ-22 — RF golden example

> Phase 9 shall remain subject to a hard build failure until one real RF example has been
> completed and approved by RF Engineering.
>
> The example shall be taken from a currently operational HTS Forward Satnet Path whose
> satellite frequency plan, Beam assignment, hub LO and IF limits can be verified against
> existing engineering data. A hypothetical software-defined payload example or data generated
> from the implementation itself is not sufficient.
>
> The approved example shall state the Payload Path Window, Beam Spectrum Assignment, RF/IF
> conversion rule, equipment limits, validity periods, requested allocation and expected
> free-capacity result. It shall also include the expected rejection of an overlapping
> allocation made through another Hub, Beam or redundant ground site when both allocations use
> the same payload input and polarization.
>
> An allocation on an independently implemented polarization may be accepted. An allocation
> outside the Beam Spectrum Assignment or its validity period must be rejected.
>
> The example closes OQ-22 only when the expected results have been calculated independently by
> an RF engineer, recorded in the golden-example file and matched exactly by the engine.

## OQ-23 — Display time zone

> UTC shall be the platform's authoritative operational and display time zone.
>
> All persisted timestamps, validity checks, overlap calculations, API values and audit records
> shall use UTC. Operational screens shall display UTC explicitly. A local time zone may be
> offered as a secondary user-interface display, but it shall not affect validation or stored
> values.
>
> An `effective_from` value that defaults to the present shall use the current UTC instant and
> shall not be rounded back to midnight. A midnight value may be used only when the user
> explicitly selects a whole-day effective period or enters that exact time.

## OQ-09 — GW ID

> GW ID is a shared reference and is not an exclusive allocatable resource.
>
> Multiple Hubs and Satnet Paths may reference the same GW ID. Double-booking shall not be
> determined from GW ID because the actual contention boundary is the underlying RF chain,
> payload input, polarization, frequency range and active period.
>
> GW ID shall no longer be free text. It shall reference a controlled Gateway record so that
> naming, payload-input association and redundancy relationships remain consistent. If a
> manufacturer-specific GW ID represents a finite physical port, that port shall be modelled as
> a separate allocatable resource rather than making every GW ID exclusive.

## OQ-10 — Decimator

> A physical Decimator shall be modelled as a time-bounded allocatable configuration resource.
>
> The same Decimator must not have two different active configurations during overlapping
> periods. A Decimator Assignment shall therefore record the Decimator, input connection,
> processed frequency range, bandwidth or decimation parameters, payload-configuration version
> and active period.
>
> Multiple Satnet Paths may reference the same Decimator Assignment where they intentionally
> consume the same processed output and the payload supports fan-out, broadcast or multicast.
> They must not create separate overlapping assignments for the same physical Decimator.
>
> Satnet Paths shall reference the Decimator Assignment rather than storing a free-text
> Decimator value.

---

## What was notable about this set

**Two of the four went in opposite directions, and the provisional position had them the same.**
The register treated OQ-09 and OQ-10 as one question — *"Is Decimator an exclusive hardware
resource? Same as OQ-09"* — and shipped both as validated free text. The answer splits them:
GW ID is a shared reference that must become a controlled foreign key and must **not** be used
for contention, while a Decimator is genuinely allocatable and needs a constraint. Treating them
alike would have been wrong in both directions at once — a false conflict on GW ID and a missed
one on the Decimator.

**OQ-22's answer is a specification for a test harness, not just a datum.** It names what the
file must contain, and half of that is beyond what the harness could run: a free-capacity
result, a rejection through *another Hub, Beam or redundant ground site* on the same payload
input, and an acceptance on an independently implemented polarization. Those are the OQ-25
answer's cases, which is presumably why they are here — the golden example is meant to prove the
reuse model end to end, not only the bandwidth arithmetic.

**OQ-23 confirmed a decision S11 had already made and flagged.** `Beam.effective_from` defaults
to the current instant rather than to midnight, and the S11 report recorded that as a sharp edge
awaiting this answer. It is now the rule.
