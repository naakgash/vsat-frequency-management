# Answers — OQ-25, OQ-26, OQ-27

**Received:** 2026-08-04
**In answer to:** `oq-25-26-27-briefing.md`
**Status:** Accepted. Recorded in `docs/design/00-assumptions-and-open-questions.md` as **A-21**
through **A-24**, and implemented per ADR-0018 and ADR-0019.

Transcribed as received. Nothing is paraphrased and nothing is omitted: this is the provenance
record for a set of decisions that reach into the database schema, and a summary would not settle an
argument about what was actually agreed.

---

## OQ-25 — Frequency reuse between Beams

> Frequency reuse shall not be determined solely by Beam identity.
>
> On hub-side and feeder-link legs, allocations compete whenever they occupy the same physical or
> logical spectrum resource. This includes a shared RF chain and a shared satellite payload input.
> Hub, antenna and geographical-site identity do not independently create reusable spectrum. Two
> redundant antennas at different sites shall therefore remain in the same payload-input spectrum
> domain when they feed the same satellite input.
>
> Orthogonal polarizations may be treated as separate spectrum resources where their RF chains are
> independently implemented.
>
> On user-Beam legs, the current HTS reuse policy shall be derived from the satellite manufacturer's
> approved Beam frequency and polarization plan. For future software-defined payloads, the Beam reuse
> or interference-domain assignment shall be associated with the active payload configuration and
> shall be time-bounded. Beam ID shall not be used as a permanent reuse boundary.
>
> An allocation may reserve more than one spectrum resource. The overlap guarantee should therefore
> be applied to spectrum-resource occupancy records rather than relying on a single Beam or Gateway
> key.

## OQ-26 — Remote-terminal equipment

> Remote-terminal equipment is not part of the mandatory allocation guarantee for the current
> platform scope.
>
> The platform shall guarantee compatibility with the configured payload path and hub-side RF/IF
> equipment. Remote-terminal compatibility shall be verified outside the hard spectrum-allocation
> constraint.
>
> A single remote equipment profile shall not be stored on the Satnet Path because the remote fleet
> is heterogeneous and a Satnet may support multiple BUC, LNB and terminal configurations. If remote
> compatibility validation is added later, it shall use optional Remote Equipment Profiles with a
> many-to-many relationship between Satnet Paths and permitted terminal profiles.

## OQ-27 — Beam sub-ranges

> A Beam may use one or more sub-ranges of its Payload Path Frequency Window.
>
> The Payload Path Frequency Window represents the maximum payload capability. The spectrum
> operationally assigned to a Beam shall be represented by separate, time-bounded Beam Spectrum
> Assignment records associated with a payload-configuration version.
>
> Every allocation must be contained within an active Beam Spectrum Assignment, both in frequency and
> time. The free-capacity engine shall calculate available capacity only within the active Beam
> assignments and not across the complete Payload Path Window.
>
> For the current fixed HTS payload, a Beam Spectrum Assignment may equal the complete Payload Path
> Window and remain continuously active. The same model shall support future software-defined
> payloads in which Beam bandwidth, frequency and channel routing change over time.

---

## A note on one phrase

The OQ-25 answer uses the words *"interference-domain assignment"* when describing software-defined
payloads. §4 of the root specification removed an entity of that name, and a repository-wide guard
rail keeps the term out of the product — entity names, labels, URLs, columns and seed values alike.

The two are not in conflict, and it is worth being exact about why. §4 removed a *configurable object
in the interface*. What this answer describes is the physical fact that object was a poor attempt at
expressing: that some allocations compete and some do not, for reasons of RF plumbing rather than of
naming. That fact is now modelled as `SpectrumResource`, on RF engineering's own term from the first
paragraph of the same answer.

The forbidden string does not appear in the schema, the interface or the code. It appears in this
file, which is a verbatim transcript and is on the guard rail's narrow allow-list for that reason —
the same allowance the design documents have, and for the same reason: a record that cannot quote its
source is not a record.
