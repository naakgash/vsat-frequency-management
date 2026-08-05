# 00 — Domain Assumptions and OPEN QUESTION Register

**Status:** Draft for engineering review
**Source of truth:** `VSAT Spectrum Allocation Platform — Root Specification v1.0`
**Rule applied:** No RF engineering value is invented. Where the specification does not fix a value,
this document records an `OPEN QUESTION` and defines only the *shape* of the data that will hold the
answer.

---

## 1. How to read this document

Two distinct categories are kept strictly apart:

| Category | Meaning | May the implementation proceed? |
|---|---|---|
| **Assumption (A-nn)** | A *structural / modelling* decision derived from the specification. It is a reading of the spec, not an engineering measurement. | Yes. Implementation proceeds on the assumption; changing it later is a schema or service change. |
| **`OPEN QUESTION` (OQ-nn)** | A *value or policy* the specification explicitly leaves open, or a contradiction discovered during design. | The **container** is built; the **value** stays empty. No default is seeded. |

Every assumption states its blast radius — what has to change if the assumption is wrong.

`OQ-01` … `OQ-24` are the specification's own open questions (§24), renumbered but kept in the
original order and wording intent. `OQ-25` onward were discovered during this design pass and are
new.

---

## 2. Domain assumptions

### Hierarchy and allocation scope

**A-01 — ~~The Beam is the frequency-reuse domain.~~ SUPERSEDED by the OQ-25 answer, 2026-08-04.**
This assumption keyed the §8.1 overlap scope on `Beam`, and it was flagged from the first design pass
as the highest-risk assumption in the model. It was wrong, and RF engineering has said so:

> *"Frequency reuse shall not be determined solely by Beam identity… allocations compete whenever they
> occupy the same physical or logical spectrum resource… Beam ID shall not be used as a permanent
> reuse boundary."*

It is replaced by **A-21**, **A-22** and **A-23**. The correction is recorded rather than edited away
because the whole point of writing a blast radius down is to be able to find what depended on it.

**A-21 — Overlap is judged on a Spectrum Resource, not on a Beam.**
A `SpectrumResource` is a physical or logical resource on which allocations compete: a shared RF
chain, a shared satellite payload input, or a leg of an approved Beam frequency-and-polarization
plan. The exclusion constraint keys on it. Gateway, Hub, antenna and site identity do **not**
independently create a reusable resource — two redundant antennas at different sites feeding the same
satellite payload input are one resource. Orthogonal polarizations are separate resources only where
their RF chains are independently implemented, which is a fact about the installation and is
therefore recorded per resource rather than derived.
*Blast radius:* this **is** the exclusion-constraint key.

**A-22 — Resources are time-bounded and tied to a payload configuration.**
A resource carries a half-open effective period. A software-defined payload changes which resources
exist and how spectrum is routed between them, so a reuse boundary that could not expire would be
wrong the moment a payload was reconfigured.
*Blast radius:* the constraint gains no column — the resource row itself is what expires — but a
resource may not be edited in place once referenced, for the reason **A-16** gives.

**A-23 — One allocation occupies many resources.**
*"An allocation may reserve more than one spectrum resource."* A Satnet Path therefore writes **N ≥ 2**
occupancy rows, not the two of a canonical/translated pair: each leg of the chain may compete on more
than one resource. The overlap guarantee is a property of the occupancy rows, and the mapping from a
Beam direction to the resources its legs occupy is configuration (`BeamDirectionSpectrumResource`).
*Blast radius:* the reservation service writes a variable number of rows in one transaction, and the
§9.5 blocking message must name which resource conflicted, not merely that something did.

**A-02 — "Spectrum leg" and "Frequency Window side" are the same concept.**
§13.6 gives Frequency Window a `side` of `HUB_UPLINK | REMOTE_DOWNLINK | REMOTE_UPLINK | HUB_DOWNLINK`;
§8.1 and §13.11 speak of a "leg". They are modelled as one enumeration, `SpectrumLeg`.
*Blast radius:* naming only; the reservation carries `leg` denormalised from its window.

**A-03 — Direction determines the two legs.**
`FWD` → `HUB_UPLINK` (selected side) + `REMOTE_DOWNLINK` (translated side).
`RTN` → `REMOTE_UPLINK` (selected side) + `HUB_DOWNLINK` (translated side).
This follows directly from §5.2/§5.3.
*Blast radius:* none; it restates the spec.

**A-04 — Polarization is an attribute of the Frequency Window, not of the allocation.**
§13.6 gives the Window a polarization, and §25 states that different polarizations are separable
"only when they are separate configured Frequency Windows". A Window therefore carries exactly one
polarization; the reservation denormalises it for the constraint key and is kept consistent by a
foreign-key-backed check.
*Blast radius:* if a Window must carry a *set* of polarizations, `SpectrumReservation.polarization`
becomes independent and the Window gains a child table. The exclusion key is unchanged either way,
which is why polarization is kept in the key despite being currently redundant.

**A-05 — A Satnet Path uses exactly one hub-side Equipment Profile. CONFIRMED by the OQ-26 answer,
2026-08-04.**
§9.4 and §13.10 refer to a single profile, a single LO and a single L-band IF range. For `FWD` this
is the hub **BUC** on the `HUB_UPLINK` leg (IF→RF); for `RTN` it is the hub **BDC/LNB** on the
`HUB_DOWNLINK` leg (RF→IF). Remote-terminal equipment is not modelled, and remote compatibility is
verified outside the hard allocation constraint.

The blast radius this assumption used to state was **wrong**, and RF engineering corrected it:

> *"A single remote equipment profile shall not be stored on the Satnet Path because the remote fleet
> is heterogeneous… it shall use optional Remote Equipment Profiles with a many-to-many relationship
> between Satnet Paths and permitted terminal profiles."*

*Blast radius:* a link table, never a second foreign key. Recorded now because the cheap-looking
change is the one that would have been made without asking.

**A-06 — ~~The Beam's Frequency Windows must be identical to its Payload Path's.~~ REVISED by the
OQ-27 answer, 2026-08-04.**
The Beam still stores explicit window foreign keys, and they must still be the Payload Path's — that
half stands, and the wizard still does not offer them as fields. What has changed is what those
windows *mean*:

> *"The Payload Path Frequency Window represents the maximum payload capability. The spectrum
> operationally assigned to a Beam shall be represented by separate, time-bounded Beam Spectrum
> Assignment records."*

The window is now the **ceiling**, not the allocation. See **A-24**.

**A-24 — A Beam's usable spectrum is its active Beam Spectrum Assignments.**
A `BeamSpectrumAssignment` is a half-open RF sub-range of one of the direction's Frequency Windows,
with its own half-open effective period, pinned to the Payload Path version it was drawn against. A
direction may hold **one or more** per window. Every allocation must be contained in an active
assignment **in frequency and in time**, and the free-capacity engine computes gaps within the active
assignments only — never across the whole window, which would report a neighbouring Beam's spectrum
as available.

The fixed-HTS case is the degenerate one: a single assignment equal to the whole window, open-ended.
That is what the platform creates by default, so today's behaviour is unchanged while the model
supports a payload whose Beam bandwidth and routing move over time.
*Blast radius:* containment validation, the gap engine's bounds, and the Satnet Path's validity
period, which must now sit inside its assignment's. Settled by **A-25**.

**A-25 — A Satnet Path lives inside the intersection of three validity periods.**
Answered by **OQ-32**, 2026-08-04. An operational Path's active period must be contained within
its Satnet's, its Beam's *and* its referenced Beam Spectrum Assignment's. The maximum permitted
period is the intersection; the service refuses anything beyond it, names the limiting parent,
and returns that maximum. Draft records may sit outside and produce warnings, but may not
reserve spectrum or become operational. The assignment must additionally belong to the same Beam
and match the Path's direction, polarization and Payload Path.

The answer required a column the platform did not have: **`Beam` had no validity period**, only
`is_active` and an activation record — although `docs/design/02` had listed it among the
effective-dated entities and `docs/design/04` named `ck_beam_effective` from the design pass.
*Blast radius:* one column on `beam`, one CHECK, and `satnets.containment` as the single place
the rule is expressed. Not a database constraint: it spans four tables and a CHECK is per-row,
which ADR-0020 records as a deliberate and stated gap.

**A-26 — GW ID is a shared reference and never a contention boundary.**
Answered by **OQ-09**, 2026-08-05. A `SatnetPath` references a controlled `Gateway` record rather
than carrying free text, so naming, payload-input association and redundancy relationships stay
consistent. It is a *reference*, not a resource:

> *"Double-booking shall not be determined from GW ID because the actual contention boundary is the
> underlying RF chain, payload input, polarization, frequency range and active period."*

Many Hubs and many Paths may name the same Gateway, and two allocations that share one do **not**
conflict on that account — which is **A-21** restated from the other side, and the reason the
gateway column appears nowhere in the exclusion key. A manufacturer-specific GW ID that stands for a
finite physical port is a `SpectrumResource`, not a stricter rule on every Gateway.
*Blast radius:* one foreign key on `satnet_path`. A test asserts that no occupancy row carries a
gateway, so a later slice cannot quietly promote the reference into a constraint.

**A-27 — A Decimator is allocated through a time-bounded Assignment.**
Answered by **OQ-10**, 2026-08-05, and it goes the *opposite* way to A-26 although the register had
treated the two questions as one. A physical `Decimator` is an allocatable configuration resource:
the same Decimator must not hold two different active configurations over overlapping periods, and
an exclusion constraint on `(decimator, active_period)` says so. A `DecimatorAssignment` records the
Decimator, its input connection, the processed frequency range, the bandwidth or decimation
parameters, the payload-configuration version and the active period.

A Satnet Path references the **assignment**, not the Decimator, and many Paths may share one where
they consume the same processed output and the payload supports fan-out, broadcast or multicast. The
thing that is forbidden is two overlapping assignments on one Decimator, which is a constraint on
the assignment table and not on how many Paths point at a row of it.
*Blast radius:* two tables, one exclusion constraint, one foreign key on `satnet_path`. Whether a
Path's validity must sit inside its assignment's active period is **OQ-36** and is not assumed.

**A-07 — The canonical operator-input side is configuration, not code.**
The operator enters one centre frequency; the other side is derived. Which leg is canonical is stored
per Beam direction (`canonical_leg`) so it can be changed without a code change. The build default is
`FWD → HUB_UPLINK`, `RTN → REMOTE_UPLINK` (uplink-canonical in both directions).
*Blast radius:* none structurally — it is a stored value. Confirmed by **OQ-28**.

### Units, arithmetic and ranges

**A-08 — All RF/IF frequencies are stored as signed 64-bit integer Hz.**
Ka-band uplink values exceed the 32-bit signed range (≈2.147 GHz), so `bigint` / `int8range` is
mandatory, not optional. Symbol rate is integer symbols/second; roll-off is `NUMERIC`; guards are
integer Hz; all timestamps are `timestamptz` stored in UTC. Binary floating point is not used
anywhere in the engineering path (§14.1).
*Blast radius:* none — this is a hard specification requirement.

**A-09 — Rounding is outward (conservative).**
§14.3 demands "one documented rounding policy" but does not state it. The policy adopted is:

```text
occupied_bw_hz = ceil( symbol_rate * (1 + rolloff) )          # never under-state occupancy
half_width_hz  = ceil( occupied_bw_hz / 2 )
occupied_start = center - half_width_hz
occupied_end   = center + half_width_hz
stored occupied_bw = occupied_end - occupied_start            # == 2 * half_width, >= computed value
allocated_start = occupied_start - left_guard
allocated_end   = occupied_end   + right_guard

# reverse mode
symbol_rate    = floor( occupied_bw_hz / (1 + rolloff) )      # never over-state capability
```

All arithmetic is `decimal.Decimal` with an explicit context; the result is always integer Hz.
Rounding is outward so the platform never under-reserves spectrum.
*Blast radius:* changes edge values by ≤1 Hz. It is a *policy*, not a measurement, but it still needs
RF sign-off — see **OQ-29**.

**A-10 — Half-open ranges everywhere, including through spectral inversion.**
RF is `[start, end)`, time is `[valid_from, valid_until)` (§8.4, §14.5). A non-inverting translation
maps `[a, b)` → `[a+Δ, b+Δ)`. An **inverting** translation `f(x) = K − x` maps `[a, b)` to the
mathematical interval `(K−b, K−a]`, which is re-normalised to `[K−b, K−a)`. Width is preserved; the
open/closed edge swaps by one representable unit (1 Hz).
*Blast radius:* one Hz at inverted-side edges. Documented in ADR-0008 and covered by a Hypothesis
round-trip property test.

**A-11 — Adjacency is legal; separation is expressed only by guard bands.**
Because ranges are half-open, `[…, 100)` and `[100, …)` do not overlap and are accepted (§25). Any
required physical separation must be modelled as a guard, never as an implicit gap.

**A-28 — UTC is authoritative for storage, validation *and* display.**
Answered by **OQ-23**, 2026-08-05. Every persisted timestamp, validity check, overlap calculation,
API value and audit record is UTC, and operational screens say so on the face of the value rather
than leaving the reader to assume it. A local time zone may later be offered as a secondary display,
but it may not reach validation or storage — which is why the display is a filter over a UTC value
and not a `timezone.activate()` on the request.

An `effective_from` or `valid_from` that defaults to the present is the **current UTC instant** and
is not rounded back to midnight; a midnight value means somebody chose a whole day. S11 had already
made that choice and flagged it as a sharp edge awaiting this answer, so nothing changes — it is now
the rule rather than a default nobody had confirmed.
*Blast radius:* `TIME_ZONE`, one display filter, and the operational templates that use it.

### Lifecycle and enforcement

**A-12 — `reserves_spectrum` is a stored boolean, not a computed predicate.**
A PostgreSQL partial index predicate must be `IMMUTABLE`, but the `SUSPENDED` policy is a runtime
setting (§15.3). The reservation therefore stores `reserves_spectrum boolean`, written by the
application service, and the exclusion constraint uses `WHERE reserves_spectrum`. A CHECK constraint
pins the statuses whose policy is *not* configurable:

```text
PLANNED, PENDING_APPROVAL, ON_AIR      => reserves_spectrum = true   (enforced)
DRAFT, CANCELLED, IMPORT_REVIEW        => reserves_spectrum = false  (enforced)
RETIRED                                => reserves_spectrum = false  (enforced)
SUSPENDED                              => either; governed by system setting
```

*Blast radius:* if `SUSPENDED` is fixed by policy rather than configurable, the CHECK tightens and the
setting is removed. See **OQ-08**.

**A-13 — Fixed reserved spectrum areas live in the reservation table.**
§16 subtracts "fixed reserve areas" from free spectrum, and a PostgreSQL exclusion constraint cannot
span two tables. `SpectrumReservation` therefore carries `kind ∈ {SATNET_PATH, FIXED_RESERVE}` with
`satnet_path_id` NULL only for `FIXED_RESERVE`. The column and constraints ship in the MVP; the
feature stays dormant until **OQ-24** is answered.
*Blast radius:* none if unused; avoids a painful later migration of a constraint-bearing table.

**A-14 — Exclusion constraints are `IMMEDIATE`, and revision writes are ordered.**
Closing the old validity period is executed *before* inserting the successor inside the same
transaction (§15.4, §15.6). Deferring the constraint was rejected: it delays the error to `COMMIT`,
which destroys field-level error attribution and holds locks longer.
*Blast radius:* service-layer write ordering; covered by a concurrency test.

**A-15 — Audit is enforced append-only in the database.**
A `BEFORE UPDATE OR DELETE` trigger on `audit_event` raises an exception. Application-level absence of
an edit path is not sufficient (§18).

**A-16 — Master-data versioning applies to the three engineering-critical entities.**
`FrequencyWindow`, `PayloadPath` and `EquipmentProfile` are versioned: a new version is a new row with
a new UUID, sharing a `version_group` UUID, with non-overlapping effective periods enforced by an
exclusion constraint. Operational records reference a *specific version row*, so history stays exact
(§13.6, §20). `Satellite`, `Band`, `Gateway`, `Hub`, `Beam` and `Satnet` use `record_version`
optimistic locking plus audit, without row-versioning.
*Blast radius:* extending versioning to Beam later requires re-pointing Satnet FKs.

**A-17 — Deny by default for object scope.**
Operator, Approver and Observer see nothing until an explicit scope grant exists. Admin bypasses
scope. Scope is conjunctive: acting on a Satnet requires the Beam **and** the Hub in scope (§6). A
Gateway grant cascades to that Gateway's Hubs; a Hub grant does not imply its Gateway.
*Blast radius:* if scope should be disjunctive, one policy function changes. See **OQ-30**.

**A-18 — Code uniqueness scopes (pending OQ-13).**
Case-insensitive throughout. Provisional scopes:

| Entity | Uniqueness scope |
|---|---|
| Satellite, Band, Gateway, EquipmentProfile | global |
| Hub | per Gateway |
| FrequencyWindow, PayloadPath | per Satellite, per version group |
| Beam | per Satellite |
| Satnet | per Beam |
| SatnetPath | per Satnet |

*Blast radius:* unique-index definitions only.

**A-29 — A golden example is external, or it is not evidence.**
Sharpened by the **OQ-22** answer, 2026-08-05. The worked example that closes §24 must come from a
currently operational HTS Forward Satnet Path whose frequency plan, Beam assignment, hub LO and IF
limits can be checked against existing engineering data:

> *"A hypothetical software-defined payload example or data generated from the implementation itself
> is not sufficient."*

And it must prove more than the bandwidth arithmetic. The file states the Payload Path Window, the
Beam Spectrum Assignment, the RF/IF conversion rule, the equipment limits, the validity periods, the
requested allocation and the expected free-capacity result — plus three outcomes that exercise
**A-21** end to end: an overlapping allocation through another Hub, Beam or redundant ground site on
the same payload input and polarization is **rejected**; one on an independently implemented
polarization **may be accepted**; one outside the Beam Spectrum Assignment or its validity period is
**rejected**.
*Blast radius:* the harness, not the engine. OQ-22 cannot be closed by building — only by a file
whose expected values an RF engineer calculated independently and which the engine then matches
exactly.

### Terminology

**A-19 — `Carrier` and `Interference Domain` are forbidden strings.**
Neither appears as an entity, label, URL, template string, column name or seed value (§4, §7). A
repository-wide terminology test enforces this on every commit, with a narrow allow-list for this
design document, which must name the forbidden terms in order to forbid them.

**A-20 — Specification Dictionary codes are immutable once referenced by application logic.**
Codes used by the calculation engine are registered in a code-side constant registry. The admin UI can
edit every human-readable attribute but the `code` field is read-only for registered entries (§2).

---

## 3. `OPEN QUESTION` register

### 3.1 Blocking for production activation — RF engineering values

No default may be invented for any item in this table. The schema holds the value; the value stays
NULL/empty until engineering confirms it.

| ID | Question | Where it lands | Blocks |
|---|---|---|---|
| **OQ-01** | Official FWD and RTN Frequency Windows for every Satellite and Beam | `inventory.FrequencyWindow` rows | Beam activation, all allocation |
| **OQ-02** | Exact satellite translation method / LO per Payload Path | `inventory.PayloadPath.translation_*` | Two-sided reservation |
| **OQ-03** | Allowed uplink/downlink polarization mappings | `inventory.PayloadPolarizationMapping` rows | Beam validation |
| **OQ-04** | BUC / BDC / LNB RF, IF and LO limits by site and model | `inventory.EquipmentProfile` rows | IF calculation, profile matching |
| **OQ-06** | Default roll-off by platform | `inventory.RolloffOption` + Satnet default | Bandwidth calculation defaults |
| **OQ-07** | Guard policy by Band, Window and platform | `inventory.GuardPolicy` rows | Allocated-bandwidth calculation |
| **OQ-14** | Circular and/or linear polarization in use | Which `PolarizationType` members are enabled per Band | Window definition |
| **OQ-22** | Validated golden worked example, **scope tightened 2026-08-05** — a real operational HTS Forward Satnet Path, never a hypothetical one, stating the window, assignment, conversion rule, equipment limits, validity, requested allocation, expected free capacity and the three reuse outcomes of **A-29** | `tests/domain/golden/` fixtures | Calculation-engine acceptance; hard build failure at Phase 9 |
| **OQ-24** | Fixed reserved spectrum areas | `SpectrumReservation(kind=FIXED_RESERVE)` rows | Free-gap correctness |

### 3.2 Blocking for design completion — policy and scope

| ID | Question | Provisional build position |
|---|---|---|
| **OQ-05** | Preferred operator input: Occupied Bandwidth or Symbol Rate | Both modes implemented (§9.2 requires both); a system setting picks the default pre-selection |
| **OQ-08** | Does `SUSPENDED` retain spectrum? | Controlled setting `SUSPENDED_RETAINS_SPECTRUM`, default **retain** (§15.3 recommends it). Built in S12 and tested both ways; ADR-0017 explains why the column is stored rather than derived. Still a position, not an answer. |
| **OQ-09** | ~~GW ID definition and uniqueness scope~~ | **ANSWERED 2026-08-05.** A shared reference, not an exclusive resource. It becomes a foreign key to a controlled `Gateway` record, and double-booking is **never** decided from it — the contention boundary is the RF chain, payload input, polarization, frequency range and active period. A GW ID that stands for a finite physical port is modelled as its own `SpectrumResource`. See **A-26** and ADR-0021. |
| **OQ-10** | ~~Is Decimator an exclusive hardware resource?~~ | **ANSWERED 2026-08-05, and *not* the same as OQ-09.** Yes: a time-bounded allocatable configuration resource. `DecimatorAssignment` records the Decimator, input connection, processed range, bandwidth/decimation parameters, payload-configuration version and active period, and no two active assignments on one Decimator may overlap in time. A Satnet Path references the *assignment*; many Paths may share one. See **A-27** and ADR-0021. |
| **OQ-11** | Is second-person approval mandatory? | Setting `REQUIRE_SEPARATE_APPROVER`, default **true**, checked against `satnet_path.created_by`. Built in S12 and tested both ways. §12's Approver role is decorative without it, which is why the default is on. |
| **OQ-12** | Are temporary/hourly future allocations required? | Time model is `timestamptz` and already supports it; UI granularity defaults to date-level |
| **OQ-13** | Code uniqueness scopes | Per **A-18** |
| **OQ-15** | Expected user and history volumes | Indexing plan assumes ≤10⁵ Satnet Paths and ≤10⁷ audit rows; partitioning deferred |
| **OQ-16** | Authentication: local or LDAP/AD | Custom `accounts.User` behind a pluggable auth backend; local-only in the MVP |
| **OQ-17** | Intranet/VPN access policy | Deployment assumes on-premises with 443 exposed only |
| **OQ-18** | Required fidelity of legacy-style Excel export | Normalized export first; legacy export slice sized after a sample workbook is supplied |
| **OQ-19** | Official RPO/RTO/retention policy | Spec §22.4 temporary targets used until confirmed |
| **OQ-20** | Availability of NMS integration APIs | Out of MVP scope; no integration surface built |
| **OQ-21** | Required service/customer/platform metadata | Satnet carries the fields named in §13.9 only |
| **OQ-23** | ~~Default display time zone~~ | **ANSWERED 2026-08-05.** UTC is the authoritative operational **and** display time zone. Persisted values, validity checks, overlap calculations, API values and audit records are UTC; operational screens display it explicitly. A local zone may be a secondary display only and may not affect validation or storage. A defaulted `effective_from` is the current UTC instant, never rounded to midnight. See **A-28** and ADR-0022. |

### 3.3 New — discovered during this design pass

| ID | Question | Why it matters | Provisional build position |
|---|---|---|---|
| **OQ-25** | ~~Is frequency reuse permitted between two Beams that share the same Gateway or Hub uplink Frequency Window?~~ | — | **ANSWERED 2026-08-04.** Reuse is not determined by Beam identity. Overlap is judged on a **Spectrum Resource** — a shared RF chain or shared satellite payload input — and Gateway, Hub, antenna and site identity do not create one. Polarizations are separate resources only where their RF chains are independent. Resources are time-bounded and tied to a payload configuration; an allocation may occupy several. See **A-21**, **A-22**, **A-23** and ADR-0018. |
| **OQ-26** | ~~Is remote-terminal equipment and its L-band IF in scope?~~ | — | **ANSWERED 2026-08-04.** Not part of the mandatory allocation guarantee. Hub-side equipment and the payload path are guaranteed; remote compatibility is verified outside the hard constraint. A single remote profile on `SatnetPath` is **explicitly ruled out** — the remote fleet is heterogeneous. Any later work uses a many-to-many of permitted terminal profiles. See **A-05**. |
| **OQ-27** | ~~May a Beam use a sub-range of its Payload Path's Frequency Window?~~ | — | **ANSWERED 2026-08-04.** Yes — one or more. The Window is the **maximum payload capability**; operationally assigned spectrum is a set of time-bounded **Beam Spectrum Assignment** records tied to a payload-configuration version. Allocations must be contained in an active assignment in frequency *and* time, and free capacity is computed within active assignments only. See **A-24** and ADR-0019. |
| **OQ-28** | Which leg is canonical for operator centre-frequency input, per direction? | Determines what the operator types and what the spectrum map shows first (§9.3). | Stored per Beam direction; default per **A-07** |
| **OQ-29** | Is outward (ceil) rounding of occupied bandwidth and half-width acceptable? | **A-09**. Affects every stored edge by ≤1 Hz and must match how the incumbent spreadsheets round, or migration comparison in Phase 9 will show spurious differences. | Outward rounding, single documented policy |
| **OQ-30** | Is Beam+Hub scope conjunctive or disjunctive, and does a Gateway grant cascade to its Hubs? | **A-17**. Determines whether an Operator with a Beam grant but no Hub grant can act. | Conjunctive; Gateway cascades to Hubs |
| **OQ-31** | Is there a tuning raster (minimum centre-frequency step) per Band, platform or modem? | Real modems tune on a raster (e.g. kHz steps). Without it, Auto-place will propose centres that no modem can be configured to. Not inventable — needs platform data. | `Band.tuning_raster_hz` nullable; when NULL, no raster is enforced and Auto-place emits an informational note |
| **OQ-32** | ~~May a Satnet Path's validity period extend beyond its Satnet's or Beam's effective period?~~ | — | **ANSWERED 2026-08-04.** No. All three containments — Satnet, Beam and Beam Spectrum Assignment — are **hard requirements** for an operational Path; the maximum permitted period is their **intersection**, and the service must name the limiting parent and return that maximum. Drafts may sit outside and warn, but must not reserve spectrum or become operational. The assignment must also match the Path's Beam, direction, polarization and Payload Path — *temporal containment alone is not sufficient*. See **A-25** and ADR-0020. |
| **OQ-33** | Does the platform reserve spectrum for a Satnet Path whose Beam has been deactivated mid-life? | Beam deactivation with live ON_AIR paths is not covered by §5 or §15. | Deactivation blocked while spectrum-reserving paths exist |
| **OQ-34** | Are minimum edge guards (§13.6) part of the allocated range or a separate validation? | If part of the range, the DB blocks edge placement; if separate, only the service does. | Separate validation against Window edges; the DB enforces containment only |
| **OQ-35** | When an Equipment Profile or Frequency Window is re-versioned, do live ON_AIR paths migrate to the new version or stay pinned? | **A-16** pins them. Migration semantics change the revision policy in §15.4. | Pinned to the referenced version; a report lists paths on superseded versions |
| **OQ-36** | Must a Satnet Path's validity sit inside its Decimator Assignment's active period? | Raised by the **OQ-10** answer, which makes the assignment time-bounded but does not say what happens when a Path outlives one. **A-25** already requires containment in three periods; a fourth would be consistent, and asserting it unasked would refuse allocations on a rule nobody confirmed. | The foreign key is recorded and **not** checked for containment. The gap is stated in ADR-0021 rather than closed by assumption. |

---

## 4. Consequence if the register is ignored

The platform is buildable to a complete, tested MVP without a single answer from §3.1: every one of
those items is a *row* in a table, not a branch in the code. What is **not** possible is production
activation — acceptance criterion §26.20 requires that unresolved RF rules be recorded rather than
guessed, and Phase 0 of the roadmap (§23) exists precisely to close §3.1 before Phase 9 cutover.

**OQ-25, OQ-26 and OQ-27 were answered on 2026-08-04** and the gate they held is lifted. The briefing
that put them is `docs/rf-confirmation/oq-25-26-27-briefing.md`; the answers are transcribed verbatim
in `docs/rf-confirmation/answers-oq-25-26-27.md`.

Two things are worth recording about how that went, because both bear on how the remaining questions
should be put.

**The briefing offered a reading, and the reading was wrong.** It proposed that the hub uplink leg be
keyed per Gateway, reasoning that a shared antenna is a shared signal. The answer says Gateway is not
the boundary at all: *"Two redundant antennas at different sites shall therefore remain in the same
payload-input spectrum domain when they feed the same satellite input."* The unit of competition is
the **satellite payload input**, and geography is irrelevant to it. A Gateway-keyed constraint would
have permitted two allocations that genuinely collide. Offering a concrete reading is what made that
correction cheap — there was something specific to reject.

**One answer was a prohibition, not a permission.** OQ-26 did not merely say "out of scope"; it ruled
out the obvious implementation — a remote profile foreign key on `SatnetPath` — and named the reason:
a heterogeneous remote fleet means one Satnet supports many terminal configurations. That is the
design mistake that would have been made silently, and it was only avoided by asking a question whose
answer was expected to be "no, skip it".
