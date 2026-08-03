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

**A-01 — The Beam is the frequency-reuse domain.**
The overlap scope in §8.1 is keyed on `Beam`. Two different Beams may therefore hold overlapping
allocations in the same Frequency Window at the same time. This is the physically expected behaviour
for spatially separated spot beams and is the direct consequence of removing `Interference Domain`
(§4).
*Blast radius:* the exclusion-constraint key. Removing `beam_id` from the key would make the whole
satellite a single pool. See **OQ-25** — this is the highest-risk assumption in the design.

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

**A-05 — A Satnet Path uses exactly one hub-side Equipment Profile.**
§9.4 and §13.10 refer to a single profile, a single LO and a single L-band IF range. For `FWD` this
is the hub **BUC** on the `HUB_UPLINK` leg (IF→RF); for `RTN` it is the hub **BDC/LNB** on the
`HUB_DOWNLINK` leg (RF→IF). Remote-terminal equipment is not modelled in the MVP.
*Blast radius:* adding remote-side equipment means a second profile FK and a second IF range on
`SatnetPath`. See **OQ-26**.

**A-06 — The Beam's direction configuration references a Payload Path, and its Frequency Windows must
be identical to that Payload Path's windows.**
§5.2/§5.3 list the windows *and* the payload path, while §13.7 already gives the Payload Path both
windows. The Beam stores explicit FKs (for query stability and audit) and a validation rule enforces
identity. Narrowing a Beam to a sub-range of a Payload Path window is **not** supported in the MVP.
*Blast radius:* supporting narrowing adds a `beam_sub_range` field and changes containment
validation. See **OQ-27**.

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
| **OQ-22** | Validated golden FWD and RTN worked examples | `tests/domain/golden/` fixtures | Calculation-engine acceptance |
| **OQ-24** | Fixed reserved spectrum areas | `SpectrumReservation(kind=FIXED_RESERVE)` rows | Free-gap correctness |

### 3.2 Blocking for design completion — policy and scope

| ID | Question | Provisional build position |
|---|---|---|
| **OQ-05** | Preferred operator input: Occupied Bandwidth or Symbol Rate | Both modes implemented (§9.2 requires both); a system setting picks the default pre-selection |
| **OQ-08** | Does `SUSPENDED` retain spectrum? | Controlled setting, default **retain** (§15.3 recommends it) |
| **OQ-09** | GW ID definition and uniqueness scope | Stored as validated metadata; promoted to `HardwareResource` only if exclusive |
| **OQ-10** | Is Decimator an exclusive hardware resource? | Same as OQ-09; `HardwareResource`/`HardwareReservation` shipped but unpopulated |
| **OQ-11** | Is second-person approval mandatory? | Setting `REQUIRE_SEPARATE_APPROVER`, default **true** |
| **OQ-12** | Are temporary/hourly future allocations required? | Time model is `timestamptz` and already supports it; UI granularity defaults to date-level |
| **OQ-13** | Code uniqueness scopes | Per **A-18** |
| **OQ-15** | Expected user and history volumes | Indexing plan assumes ≤10⁵ Satnet Paths and ≤10⁷ audit rows; partitioning deferred |
| **OQ-16** | Authentication: local or LDAP/AD | Custom `accounts.User` behind a pluggable auth backend; local-only in the MVP |
| **OQ-17** | Intranet/VPN access policy | Deployment assumes on-premises with 443 exposed only |
| **OQ-18** | Required fidelity of legacy-style Excel export | Normalized export first; legacy export slice sized after a sample workbook is supplied |
| **OQ-19** | Official RPO/RTO/retention policy | Spec §22.4 temporary targets used until confirmed |
| **OQ-20** | Availability of NMS integration APIs | Out of MVP scope; no integration surface built |
| **OQ-21** | Required service/customer/platform metadata | Satnet carries the fields named in §13.9 only |
| **OQ-23** | Default display time zone | Storage UTC; display time zone a system setting, unset until confirmed |

### 3.3 New — discovered during this design pass

| ID | Question | Why it matters | Provisional build position |
|---|---|---|---|
| **OQ-25** | Is frequency reuse permitted between two Beams that share the same Gateway or Hub uplink Frequency Window? | Removing `Interference Domain` (§4) makes `Beam` the reuse key (**A-01**). Two Beams fed from the *same* gateway antenna and the same hub-uplink Window would be allowed to overlap by the constraint, which may be physically wrong. This is the single largest correctness risk in the model. | Beam-keyed exclusion as specified; a warning is raised (not blocked) when two Beams share a Gateway and a `HUB_UPLINK` Window with overlapping RF |
| **OQ-26** | Is remote-terminal equipment (remote BUC / remote LNB) and its L-band IF in scope? | **A-05** models hub-side equipment only. Remote-side IF limits could invalidate placements that the platform accepts. | Hub-side only; second profile FK is an additive migration |
| **OQ-27** | May a Beam use a sub-range of its Payload Path's Frequency Window? | **A-06** requires identity. If Beams must be narrowed to a portion of a shared transponder, containment validation and the gap engine both change. | Identity required |
| **OQ-28** | Which leg is canonical for operator centre-frequency input, per direction? | Determines what the operator types and what the spectrum map shows first (§9.3). | Stored per Beam direction; default per **A-07** |
| **OQ-29** | Is outward (ceil) rounding of occupied bandwidth and half-width acceptable? | **A-09**. Affects every stored edge by ≤1 Hz and must match how the incumbent spreadsheets round, or migration comparison in Phase 9 will show spurious differences. | Outward rounding, single documented policy |
| **OQ-30** | Is Beam+Hub scope conjunctive or disjunctive, and does a Gateway grant cascade to its Hubs? | **A-17**. Determines whether an Operator with a Beam grant but no Hub grant can act. | Conjunctive; Gateway cascades to Hubs |
| **OQ-31** | Is there a tuning raster (minimum centre-frequency step) per Band, platform or modem? | Real modems tune on a raster (e.g. kHz steps). Without it, Auto-place will propose centres that no modem can be configured to. Not inventable — needs platform data. | `Band.tuning_raster_hz` nullable; when NULL, no raster is enforced and Auto-place emits an informational note |
| **OQ-32** | May a Satnet Path's validity period extend beyond its Satnet's or Beam's effective period? | §13.9 forbids a Satnet outliving its Beam but is silent for Paths. Determines whether containment is a CHECK or a warning. | Containment enforced at service level, warned in UI, not a DB constraint |
| **OQ-33** | Does the platform reserve spectrum for a Satnet Path whose Beam has been deactivated mid-life? | Beam deactivation with live ON_AIR paths is not covered by §5 or §15. | Deactivation blocked while spectrum-reserving paths exist |
| **OQ-34** | Are minimum edge guards (§13.6) part of the allocated range or a separate validation? | If part of the range, the DB blocks edge placement; if separate, only the service does. | Separate validation against Window edges; the DB enforces containment only |
| **OQ-35** | When an Equipment Profile or Frequency Window is re-versioned, do live ON_AIR paths migrate to the new version or stay pinned? | **A-16** pins them. Migration semantics change the revision policy in §15.4. | Pinned to the referenced version; a report lists paths on superseded versions |

---

## 4. Consequence if the register is ignored

The platform is buildable to a complete, tested MVP without a single answer from §3.1: every one of
those items is a *row* in a table, not a branch in the code. What is **not** possible is production
activation — acceptance criterion §26.20 requires that unresolved RF rules be recorded rather than
guessed, and Phase 0 of the roadmap (§23) exists precisely to close §3.1 before Phase 9 cutover.

The items in §3.3 are different in kind: **OQ-25**, **OQ-26** and **OQ-27** can change the database
schema and the exclusion-constraint key. They should be answered before Slice 8 (the reservation
engine) rather than before cutover.
