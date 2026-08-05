# ADR-0021 — A GW ID is a reference; a Decimator is allocated through an Assignment

**Status:** Accepted
**Date:** 2026-08-05
**Slice:** S11a — Controlled hardware references and UTC
**Specification:** §9.4, §13.3, §13.10, §20
**Answers:** **OQ-09**, **OQ-10**
**Assumptions:** **A-21**, **A-26**, **A-27**
**Raises:** **OQ-36**

## Context

S11 shipped `gw_id` and `decimator` on `SatnetPath` as validated free text, with a comment
saying why: modelling either as an exclusive resource would have invented an exclusivity rule
nobody had confirmed, and the platform would then have started refusing allocations on it.

The register treated the two as one question. **OQ-10** was recorded verbatim as *"Is Decimator
an exclusive hardware resource? Same as OQ-09"*, and the provisional position for both was
`HardwareResource` / `HardwareReservation`, *"shipped but unpopulated"*.

They are not the same question. The answers go in opposite directions.

> **OQ-09.** *"GW ID is a shared reference and is not an exclusive allocatable resource…
> Double-booking shall not be determined from GW ID because the actual contention boundary is
> the underlying RF chain, payload input, polarization, frequency range and active period.
> GW ID shall no longer be free text. It shall reference a controlled Gateway record."*

> **OQ-10.** *"A physical Decimator shall be modelled as a time-bounded allocatable
> configuration resource. The same Decimator must not have two different active configurations
> during overlapping periods."*

Deferring was the right call, and this is why. Building `HardwareResource` for both would have
been wrong twice at once: a false conflict every time two Satnets shared a Gateway, and no
conflict at all when one Decimator was configured two ways over the same week.

## Decision

### GW ID becomes a foreign key that nothing contends on

`SatnetPath.gateway` references `inventory.Gateway`, with `verbose_name = "GW ID"`. It is
optional, and it is **absent from every occupancy row and every exclusion key** — which is
**A-21** seen from the other side: the resource decides, and the site does not.

Two tests hold that open. One is behavioural — two allocations at the same frequency, at the
same time, through the same Gateway, both accepted because their Beams' legs map to different
Spectrum Resources — and one is structural, asserting that `SpectrumReservation` carries no
gateway, hub or site column and that `excl_reservation_overlap` still keys on exactly
`(spectrum_resource, allocated_rf, active_period)`. The structural one matters more than it
looks: a behavioural test alone would keep passing on the day somebody added the column, and
would only fail later, looking like a spectrum bug.

The answer's escape hatch is recorded rather than built: *"if a manufacturer-specific GW ID
represents a finite physical port, that port shall be modelled as a separate allocatable
resource"*. That resource is a `SpectrumResource`, which already exists and already ships
empty — no new table, and no rule applied to every Gateway on the strength of one platform.

### A Decimator is a box; a Decimator Assignment is what holds it

Two tables:

* **`Decimator`** — the physical unit at a Hub. Identity only.
* **`DecimatorAssignment`** — the decimator, its input connection, the processed frequency
  range, the bandwidth or decimation parameters, the payload-configuration version and the
  active period. Exactly the fields the answer lists.

```sql
ALTER TABLE decimator_assignment
  ADD CONSTRAINT excl_decimator_assignment_overlap
  EXCLUDE USING gist (decimator_id WITH =, active_period WITH &&)
  WHERE (is_active);
```

`SatnetPath.decimator_assignment` points at the **assignment**, not the box. That is what the
answer asks for and it is also what makes the model work: several Paths may consume one
assignment — *"where they intentionally consume the same processed output and the payload
supports fan-out, broadcast or multicast"* — so exclusivity cannot live on the Path's foreign
key. It lives on the assignment table, where it belongs, and the plain FK gives fan-out for
free.

The `is_active` condition mirrors `excl_window_version_overlap`: a withdrawn configuration is a
record of what used to be true, and a constraint that refused to keep it would push the
platform towards deleting history to make room.

## What is deliberately not enforced

**No parameter is mandatory.** `channel_bandwidth_hz` and `decimation_factor` are both
nullable, with no CHECK demanding one of them. The answer says *"bandwidth or decimation
parameters"*, which parameterisation applies is a fact about the equipment, and nothing in the
platform computes from either column — so there is no silent-zero failure mode of the kind that
justifies `ck_guard_mode_has_required_values`. Refusing a row that carries neither would be a
rule nobody confirmed.

**A Path's validity is not contained in its assignment's active period.** **A-25** already
requires containment in three periods, and a fourth would be consistent — but the answer does
not say so, and asserting it unasked would refuse allocations on an invented rule. That is
**OQ-36**, and it is a stated gap rather than a closed one.

**Neither reference is required.** A Path with no Gateway and no Decimator Assignment recorded
is legitimate; demanding either would block allocations on information nobody has yet.

## Consequences

`docs/design/02` §8 listed `HardwareResource` / `HardwareReservation` as a shipped-and-dormant
pair gated on these two questions. Neither is built, and neither will be: the answers replaced
one generic table with a controlled reference on one side and a purpose-shaped pair on the
other. The row in that document is corrected rather than deleted, for the same reason **A-01**
is struck through rather than removed — a superseded position is worth being able to find.

`docs/design/04` §3.4's dormant `excl_hardware_overlap` sketch becomes the real
`excl_decimator_assignment_overlap` above.

Nothing seeds a `Decimator` or a `DecimatorAssignment`, and no screen creates one in this
slice. Which decimators exist and how they are configured is site data (§26.20); the wizard
offers whatever is there, which today is nothing.
