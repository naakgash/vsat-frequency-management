# ADR-0018 — Overlap is judged on a Spectrum Resource, not on a Beam

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S9a — Spectrum Resources and Beam Spectrum Assignments
**Specification:** §4, §8.1, §8.3, §13.11
**Answers:** **OQ-25**
**Supersedes:** **A-01** (the Beam-keyed reuse domain)
**Assumptions:** **A-21**, **A-22**, **A-23**

## Context

The platform's central promise is that two allocations cannot occupy the same Hz at the
same time. That promise is one PostgreSQL exclusion constraint, and a constraint has a
**key** — the list of columns that must match before two rows are considered to be
competing.

§4 of the specification removed the object that used to express this and did not replace
it. The design pass therefore keyed the constraint on `Beam` (**A-01**) and recorded, from
the first document onward, that this was the single largest correctness risk in the model:
it is right for two spot beams pointing at different parts of the earth and potentially very
wrong for two Beams sharing one uplink chain.

That risk was put to RF engineering as **OQ-25**, with four candidate answers and a proposed
reading. The answer rejected the proposed reading:

> Frequency reuse shall not be determined solely by Beam identity… allocations compete
> whenever they occupy the same physical or logical spectrum resource. This includes a shared
> RF chain and a shared satellite payload input. **Hub, antenna and geographical-site
> identity do not independently create reusable spectrum.**

The briefing had proposed keying the hub uplink leg per **Gateway**, on the reasoning that a
shared antenna is a shared signal. That is not the boundary. Two redundant antennas at
*different sites* feeding the same satellite payload input are one resource — the unit of
competition is the payload input, and geography has nothing to do with it. A Gateway-keyed
constraint would have accepted allocations that genuinely collide.

## Decision

**The exclusion constraint keys on `spectrum_resource_id`.**

```sql
EXCLUDE USING gist (
    spectrum_resource_id WITH =,
    allocated_rf         WITH &&,
    active_period        WITH &&
)
WHERE (reserves_spectrum)
```

Three columns where the superseded design had six. Everything that used to be in the key —
Beam, Frequency Window, leg, polarization — is now either a property *of* a resource or
irrelevant to whether two allocations compete. The complexity has not gone away; it has
moved to the question of **which resources an allocation occupies**, which is configuration
that an administrator can see and change, rather than a constraint definition that only a
migration can.

**A `SpectrumResource` is master data with a half-open effective period.** It carries the
satellite, the leg, an optional polarization, and a source reference naming the plan it came
from. Polarization is **nullable and meaningful when null**: the answer says orthogonal
polarizations are separate resources *"where their RF chains are independently implemented"*,
which is a fact about a particular installation. A null means the resource is not
polarization-separated, and that is a recorded engineering statement rather than missing
data.

**Resources expire.** §OQ-25 requires that for software-defined payloads the reuse
assignment *"be associated with the active payload configuration and shall be time-bounded"*.
A resource therefore has `effective_from` / `effective_until` and a generated
`effective_period`, and is superseded rather than edited once referenced, for the reason
**A-16** gives about every other engineering-critical record.

**A Beam direction declares which resources each of its legs occupies**, through
`BeamDirectionSpectrumResource`. This is the join that replaces the old implicit "the Beam is
the pool". It is explicit because it is the thing an engineer needs to be able to inspect
and correct: if two Beams should compete and do not, this table is where the answer is
wrong, and no amount of reading the constraint definition would reveal it.

**An allocation writes one occupancy row per resource per leg**, N ≥ 2 rather than exactly
two. §OQ-25 is explicit: *"An allocation may reserve more than one spectrum resource."* The
two-sided reservation of ADR-0006 remains true about the *engineering* — one side is
calculated and the other is its image — but it is no longer the row count.

### On the term this replaces

§4 removed a named, configurable "domain" object from the product, and **A-19** keeps the
string out of the schema, the interface and the code. The OQ-25 answer uses that phrase once,
in passing, while describing software-defined payloads.

There is no conflict, and it is worth being exact about why rather than leaving it to be
rediscovered. §4 removed an *interface object*. What the answer describes is the physical
fact that object was a poor attempt at naming: some allocations compete and some do not, for
reasons of RF plumbing. That fact is not optional and cannot be designed away — the
alternative to modelling it is guessing at it, which is what **A-01** was doing. It is
modelled as `SpectrumResource`, which is RF engineering's own term from the first paragraph
of the same answer, and it is not a configurable grouping an operator assembles: it is a
record of how the payload and the RF chains are actually built.

## Consequences

**What this buys.** A constraint that encodes the physical situation instead of a proxy for
it. A three-column key is also easier to reason about and index than a six-column one, and
the GiST index it needs is smaller.

**What it costs.**

*Resources have to exist before anything can be allocated.* There is no sensible default —
inferring one resource per Beam would reinstate **A-01** under a new name, and inferring one
per satellite would forbid all reuse. So the table ships **empty**, a Beam direction with no
resource mapping fails validation with a message that says so, and no Beam can be activated
until an engineer has stated what competes with what. That is a real setup cost and it is the
correct one: the alternative is a platform that guesses at interference.

**A hard consequence worth stating plainly:** every Beam built before this slice becomes
invalid until its legs are mapped to resources. The identity-based model gave an answer for
free that was not ours to give.

*The blocking message is harder to write.* §9.5 requires the refusal to name the conflicting
allocation. With N resources it must also name *which resource* conflicted, or an operator
sees "this overlaps" without being able to tell which of three shared chains is the problem.

*A resource is only as good as the engineer who defined it.* The database will enforce
whatever mapping it is given, exactly and forever. A missing `BeamDirectionSpectrumResource`
row is a silent permission to interfere, and no constraint can detect it — which is why the
mapping is a validation rule with a finding, not merely an optional field.

**What it forecloses.** Deriving competition from anything the platform already knows. There
was an appeal to that — Beam, Gateway, Window are all *there* — and the answer is that none
of them is the physical unit.

## Alternatives considered

**Keep the Beam key and warn on shared Gateways** — the interim behaviour up to S8. Rejected
by the answer directly. A warning on a rule the platform will not enforce is a note that an
allocation might be wrong, in a system whose entire purpose is to be certain.

**Key on Gateway for hub-side legs and Beam for remote legs** — the briefing's own proposal.
Rejected: redundant antennas at different sites feeding one payload input must compete, and a
Gateway key would let them through.

**Key on `frequency_window_id` alone** — rejected. A window is a range of spectrum on a
satellite leg, not a piece of plumbing; two Beams may legitimately reuse the same window when
their chains are independent, which is precisely the case the answer preserves for user-Beam
legs.

**Derive resources from an equipment/RF-chain graph** — modelling amplifiers, feeds and
inputs and computing the shared resources from the topology. Rejected as inventing an
engineering model nobody asked for: the answer says a resource is what the approved plan says
it is, and a derived graph would be a second source of truth to keep in step with the first.
