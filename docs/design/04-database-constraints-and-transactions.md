# 04 — Database Constraint and Transaction Design

**Derived from:** Root Specification §8.3, §8.4, §14, §15.6, §20.
**Governing rule (§8.3):** *"Keep PostgreSQL as the final defense layer… The UI and service layer must
perform pre-checks to explain errors, but they must not be the only protection."*

Everything in this document is enforced by the database. Service-layer checks exist to produce good
error messages; if a service check and a database constraint ever disagree, the database is right.

---

## 1. Prerequisites

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- uuid/text equality inside a GiST index
CREATE EXTENSION IF NOT EXISTS citext;       -- case-insensitive human-readable codes
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid() on PG < 13; no-op on 16
```

`btree_gist` is not optional: the exclusion constraint mixes `uuid`/`text` equality operators with
range overlap operators in one GiST index, which the stock GiST opclasses cannot do.

Deployment consequence: the application database user needs the extension at migration time. The
first migration creates it, and `/health/ready` asserts its presence so a mis-provisioned database
fails loudly rather than silently losing the overlap protection.

---

## 2. Range representation

Scalar bounds are the canonical, form-bindable columns; the range column is **generated**, so the two
can never drift:

```sql
allocated_start_hz  bigint    NOT NULL,
allocated_end_hz    bigint    NOT NULL,
allocated_rf        int8range NOT NULL
    GENERATED ALWAYS AS (int8range(allocated_start_hz, allocated_end_hz, '[)')) STORED,

valid_from    timestamptz NOT NULL,
valid_until   timestamptz NULL,
active_period tstzrange   NOT NULL
    GENERATED ALWAYS AS (tstzrange(valid_from, valid_until, '[)')) STORED
```

Notes:

- `bigint` is mandatory — Ka-band uplink in Hz exceeds the 32-bit signed maximum (**A-08**).
- `'[)'` bounds implement §8.4 / §14.5 half-open semantics directly in the type system, so
  adjacency (`[…,100)` next to `[100,…)`) is non-overlapping **by construction** rather than by
  application arithmetic (**A-11**).
- `valid_until IS NULL` produces an unbounded upper bound, which `&&` handles natively.
- Generated columns require `IMMUTABLE` expressions; the range constructors qualify. A migration test
  asserts this on the target PostgreSQL version rather than trusting it.

---

## 3. The overlap constraint

### 3.1 Definition

**Revised 2026-08-04 by the OQ-25 answer.** The Beam-keyed key below was the design up to S8 and is
recorded in ADR-0018 as superseded. Reuse is not determined by Beam identity: allocations compete
when they occupy the same **Spectrum Resource**.

```sql
ALTER TABLE spectrum_reservation
  ADD CONSTRAINT excl_reservation_overlap
  EXCLUDE USING gist (
      spectrum_resource_id WITH =,
      allocated_rf         WITH &&,
      active_period        WITH &&
  )
  WHERE (reserves_spectrum);
```

Three columns where the superseded design had six. Everything that used to be in the key — Beam,
Frequency Window, leg, polarization — is now a property *of* a resource or irrelevant to whether two
allocations compete:

- **Beam** is out. *"Beam ID shall not be used as a permanent reuse boundary."*
- **Gateway is not a substitute.** *"Two redundant antennas at different sites shall remain in the
  same payload-input spectrum domain when they feed the same satellite input."* Geography is not the
  unit of competition; the payload input is.
- **Leg** is a property of a resource — a hub uplink RF chain and a remote downlink are different
  physical things and are therefore different rows.
- **Polarization** is a property too, and only sometimes: *"orthogonal polarizations may be treated
  as separate spectrum resources where their RF chains are independently implemented."* Two
  resources when they are, one when they are not.

The complexity has not gone away — it has moved to **which resources an allocation occupies**, which
is configuration in `beam_direction_spectrum_resource` that an administrator can inspect and correct,
rather than a constraint definition only a migration can change.

**One allocation writes N ≥ 2 occupancy rows**, not two: *"an allocation may reserve more than one
spectrum resource"* (**A-23**). The two-sided reservation of ADR-0006 is still true of the
*engineering* — one side is calculated and the other is its image — but it is no longer the row
count, and the §9.5 blocking message must name **which resource** conflicted.

Four design points:

1. **`allocated_rf`, not `occupied_rf`.** §8.1 says the reserved interval includes guard bands, and
   §8.2 says "do not check only the centre frequency". The occupied range is stored for display and
   for the `occupied ⊂ allocated` check, but it is not what the constraint compares.
2. **`WHERE (reserves_spectrum)` uses a stored boolean, not an expression over `status`.** A partial
   index predicate must be `IMMUTABLE`, and the `SUSPENDED` policy is a runtime setting (§15.3).
   The column is written by the service and pinned by a CHECK for every status whose policy is fixed
   (**A-12**).
3. **Not `DEFERRABLE`.** Immediate checking attributes the violation to the offending statement, which
   is what makes the §9.5 error message possible. Revision writes are ordered close-then-open instead
   (**A-14**).
4. **`kind` is not in the key** — a `FIXED_RESERVE` block and a Satnet Path allocation must exclude
   each other, which is the whole reason they share a table (**A-13**).

### 3.1.1 Containment moved too

Under the superseded design an allocation had to fit inside its Frequency Window. The OQ-27 answer
makes the Window the **maximum payload capability**, and the operational bound a
`beam_spectrum_assignment` — a sub-range with its own effective period (**A-24**, ADR-0019).

So containment is now **two-dimensional**: an allocation must sit inside an assignment in frequency
*and* in time. Checking the RF and forgetting the period gives an allocation that is valid today and
silently outside its assignment next month, which is why the service resolves both together and
returns the assignment it matched rather than letting each caller re-derive it.

The assignment's own containment inside its window is a per-row CHECK
(`ck_assignment_within_window`), made possible by carrying the window's edges on the assignment and
pinning the copy with a composite foreign key against `frequency_window (id, rf_start_hz,
rf_end_hz)` — the same device §3.2 describes for the Payload Path's window sides, for the same
reason.

Free capacity follows: gaps are computed within **active assignments**, never across the whole
window, or the engine reports a neighbouring Beam's spectrum as available.

### 3.2 Why the key columns cannot be trusted as plain denormalisation

`beam_id`, `leg` and `polarization` are copied onto the reservation so the constraint key exists. If
any copy were wrong, the constraint would silently protect the wrong scope. They are therefore
enforced by **composite foreign keys**, not by application discipline:

```sql
-- targets
ALTER TABLE frequency_window ADD CONSTRAINT uq_fw_id_side_pol
  UNIQUE (id, side, polarization);
ALTER TABLE satnet_path      ADD CONSTRAINT uq_sp_id_beam UNIQUE (id, beam_id);
ALTER TABLE satnet           ADD CONSTRAINT uq_sn_id_beam UNIQUE (id, beam_id);
ALTER TABLE hub              ADD CONSTRAINT uq_hub_id_gateway UNIQUE (id, gateway_id);

-- enforcement
ALTER TABLE spectrum_reservation ADD CONSTRAINT fk_res_window_leg_pol
  FOREIGN KEY (frequency_window_id, leg, polarization)
  REFERENCES frequency_window (id, side, polarization);

ALTER TABLE spectrum_reservation ADD CONSTRAINT fk_res_path_beam
  FOREIGN KEY (satnet_path_id, beam_id)
  REFERENCES satnet_path (id, beam_id);        -- MATCH SIMPLE: not enforced when path is NULL

ALTER TABLE satnet_path ADD CONSTRAINT fk_path_satnet_beam
  FOREIGN KEY (satnet_id, beam_id) REFERENCES satnet (id, beam_id);

ALTER TABLE satnet ADD CONSTRAINT fk_satnet_hub_gateway
  FOREIGN KEY (hub_id, gateway_id) REFERENCES hub (id, gateway_id);
```

The `MATCH SIMPLE` default is exactly the behaviour wanted for `FIXED_RESERVE` rows, where
`satnet_path_id` is NULL and the composite FK is skipped.

The same technique pins Payload Path window sides, which §20 requires as "Frequency Window side
consistency" and which is otherwise a cross-table rule needing a trigger:

```sql
ALTER TABLE frequency_window ADD CONSTRAINT uq_fw_id_side UNIQUE (id, side);

ALTER TABLE payload_path
  ADD COLUMN uplink_window_side   text NOT NULL,
  ADD COLUMN downlink_window_side text NOT NULL,
  ADD CONSTRAINT fk_pp_uplink_side
    FOREIGN KEY (uplink_window_id, uplink_window_side)   REFERENCES frequency_window (id, side),
  ADD CONSTRAINT fk_pp_downlink_side
    FOREIGN KEY (downlink_window_id, downlink_window_side) REFERENCES frequency_window (id, side),
  ADD CONSTRAINT ck_pp_direction_sides CHECK (
      (direction = 'FWD' AND uplink_window_side = 'HUB_UPLINK'
                         AND downlink_window_side = 'REMOTE_DOWNLINK')
   OR (direction = 'RTN' AND uplink_window_side = 'REMOTE_UPLINK'
                         AND downlink_window_side = 'HUB_DOWNLINK')
  );
```

Django does not model composite foreign keys, so these ship as `migrations.RunSQL` with matching
reverse SQL, alongside `UniqueConstraint`s that Django *does* model.

### 3.3 Master-data version non-overlap

Two versions of the same Frequency Window, Payload Path or Equipment Profile must not be
simultaneously active (**A-16**):

```sql
ALTER TABLE frequency_window
  ADD CONSTRAINT excl_fw_version_overlap
  EXCLUDE USING gist (version_group WITH =, effective_period WITH &&)
  WHERE (is_active);
```

Repeated identically on `payload_path` and `equipment_profile`.

### 3.4 Hardware exclusivity (dormant until OQ-09 / OQ-10)

```sql
ALTER TABLE hardware_reservation
  ADD CONSTRAINT excl_hardware_overlap
  EXCLUDE USING gist (resource_id WITH =, active_period WITH &&)
  WHERE (is_exclusive);
```

---

## 4. CHECK constraints

§20 lists nine minimum checks. All nine plus the ones needed by the calculation model:

### `spectrum_reservation`

| Name | Definition | §20 item |
|---|---|---|
| `ck_res_alloc_start_lt_end` | `allocated_start_hz < allocated_end_hz` | start < end |
| `ck_res_occ_start_lt_end` | `occupied_start_hz < occupied_end_hz` | start < end |
| `ck_res_occ_in_alloc` | `allocated_rf @> occupied_rf` | occupied inside allocated |
| `ck_res_not_empty` | `NOT isempty(allocated_rf) AND NOT isempty(active_period)` | — |
| `ck_res_period` | `valid_until IS NULL OR valid_from < valid_until` | valid_from < valid_until |
| `ck_res_leg` | `leg IN ('HUB_UPLINK','REMOTE_DOWNLINK','REMOTE_UPLINK','HUB_DOWNLINK')` | controlled enums |
| `ck_res_direction_leg` | `(direction='FWD' AND leg IN ('HUB_UPLINK','REMOTE_DOWNLINK')) OR (direction='RTN' AND leg IN ('REMOTE_UPLINK','HUB_DOWNLINK')) OR (kind='FIXED_RESERVE' AND direction IS NULL)` | side consistency |
| `ck_res_kind_path` | `(kind='SATNET_PATH' AND satnet_path_id IS NOT NULL AND status IS NOT NULL) OR (kind='FIXED_RESERVE' AND satnet_path_id IS NULL)` | — |
| `ck_res_reserves_status` | `(status IN ('PLANNED','PENDING_APPROVAL','ON_AIR') AND reserves_spectrum) OR (status IN ('DRAFT','CANCELLED','IMPORT_REVIEW','RETIRED') AND NOT reserves_spectrum) OR status = 'SUSPENDED' OR status IS NULL` | **A-12**, §15.3 |

### `satnet_path`

| Name | Definition |
|---|---|
| `ck_path_symbol_rate` | `symbol_rate_sps IS NULL OR symbol_rate_sps > 0` (§20: symbol rate > 0) |
| `ck_path_rolloff` | `rolloff >= 0 AND rolloff <= 1` — definitional bound for a raised-cosine roll-off factor; the *operationally allowed* set is narrower and admin-managed (**OQ-06**) |
| `ck_path_guards` | `guard_left_hz >= 0 AND guard_right_hz >= 0` (§20: guard ≥ 0) |
| `ck_path_center_in_occupied` | `canonical_occupied_rf @> canonical_center_hz AND translated_occupied_rf @> translated_center_hz` (§20: centre inside occupied) |
| `ck_path_occ_in_alloc` | `canonical_allocated_rf @> canonical_occupied_rf` and the same on the translated side (§20: occupied inside allocated) |
| `ck_path_validity` | `valid_until IS NULL OR valid_from < valid_until` |
| `ck_path_status` | `status IN (…8 values…)` |
| `ck_path_direction` | `direction IN ('FWD','RTN')` |
| `ck_path_legs` | canonical/translated legs match `direction` per **A-03** |
| `ck_path_input_mode` | `input_mode IN ('OCCUPIED_BW','SYMBOL_RATE') AND input_value > 0` — §9.2 forbids both being independently editable, so exactly one mode and one value is stored |
| `ck_path_bw_positive` | `occupied_bw_hz > 0 AND allocated_bw_hz >= occupied_bw_hz` |
| `ck_path_if` | `if_start_hz IS NULL OR (if_start_hz < if_end_hz AND if_start_hz >= 0)` |
| `ck_path_revision` | `revision_number >= 1 AND (supersedes_id IS NULL) = (revision_number = 1)` |

### `frequency_window`

`ck_fw_start_lt_end` (`rf_start_hz < rf_end_hz`, §13.6), `ck_fw_edge_guard` (`min_edge_guard_hz >= 0`),
`ck_fw_side`, `ck_fw_polarization`, `ck_fw_effective` (`effective_until IS NULL OR effective_from <
effective_until`).

### `equipment_profile`

`ck_eq_rf_range` (`rf_min_hz < rf_max_hz`), `ck_eq_if_range` (`if_min_hz < if_max_hz`),
`ck_eq_lo_positive` (`lo_hz > 0`), `ck_eq_type`, `ck_eq_conversion`, `ck_eq_sideband`, and
`ck_eq_conversion_sideband` tying `LO_MINUS_IF` to `HIGH_SIDE` and `LO_PLUS_IF` to `LOW_SIDE`.

### `band`, `beam`, `satnet`

`ck_band_rf_range`, `ck_band_raster` (`tuning_raster_hz IS NULL OR tuning_raster_hz > 0`,
**OQ-31**), `ck_beam_effective`, `ck_satnet_effective`. Satnet-within-Beam period containment
(§13.9) is a cross-row rule and is enforced in the service layer plus a nightly consistency check,
not as a CHECK.

---

## 5. Uniqueness

Codes are `citext`, giving case-insensitive uniqueness without functional indexes (**A-18**,
**OQ-13**):

```text
satellite(code)                                       unique
band(code)                                            unique
gateway(code)                                         unique
equipment_profile(code, version_number)               unique
hub(gateway_id, code)                                 unique
frequency_window(satellite_id, code, version_number)  unique
payload_path(satellite_id, code, version_number)      unique
beam(satellite_id, code)                              unique
satnet(beam_id, code)                                 unique
satnet_path(satnet_id, code)                          unique
beam_direction_config(beam_id, direction)             unique
specification_definition(code)                        unique
user_beam_scope(user_id, beam_id)                     unique   (and the two sibling scope tables)
payload_polarization_mapping(payload_path_id, uplink_polarization, downlink_polarization) unique
```

Plus one partial unique index enforcing "at most one current version per group":

```sql
CREATE UNIQUE INDEX uq_fw_current_version
  ON frequency_window (version_group)
  WHERE is_active AND effective_until IS NULL;
```

---

## 6. Indexes (§20)

| Table | Index | Purpose |
|---|---|---|
| `spectrum_reservation` | the GiST exclusion index itself | overlap check + occupancy scans |
| `spectrum_reservation` | `(frequency_window_id, leg, polarization) WHERE reserves_spectrum` | gap engine hot path |
| `spectrum_reservation` | `(satnet_path_id)`, `(beam_id, direction)` | detail joins, Beam utilisation |
| `satnet_path` | `(satnet_id, status)`, `(beam_id, direction, status)` | list filters |
| `satnet_path` | GiST on `active_period`, GiST on `canonical_allocated_rf` | time and RF filtering in the spectrum view |
| `satnet_path` | `(revision_group, revision_number)` | history view |
| `satnet_path` | `(status) WHERE status = 'PENDING_APPROVAL'` | approval queue |
| `satnet` | `(beam_id)`, `(hub_id)`, `(gateway_id)` | scope filtering |
| `frequency_window` | `(satellite_id, side, polarization) WHERE is_active` | window pickers |
| `payload_path` | `(satellite_id, direction) WHERE is_active` | Beam Builder |
| `equipment_profile` | `(band_id, type, priority) WHERE is_active` | profile matching |
| `audit_event` | `(occurred_at DESC)`, `(object_type, object_id, occurred_at DESC)`, `(actor_id, occurred_at DESC)` | audit search (§18) |
| `audit_event` | GIN on `before`, `after` JSONB | field-level diff search |
| `import_row` | `(batch_id, classification)` | import result report |
| search fields | trigram GIN on `name`/`code` where free-text search is offered | §20 "search fields" |

---

## 7. Triggers

Exactly two. Triggers are avoided elsewhere because they hide behaviour from the service layer; these
two encode rules that must hold even against direct SQL.

```sql
-- 1. Audit is append-only (§18, A-15)
CREATE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only (attempted %)', TG_OP
    USING ERRCODE = 'restrict_violation';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_event_immutable
  BEFORE UPDATE OR DELETE ON audit_event
  FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();

-- 2. Optimistic locking guard (§15.5) — record_version must strictly increase
CREATE FUNCTION record_version_increments() RETURNS trigger AS $$
BEGIN
  IF NEW.record_version <= OLD.record_version THEN
    RAISE EXCEPTION 'record_version must increase (% -> %)',
      OLD.record_version, NEW.record_version USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
```

Trigger 2 is attached to every `RecordVersioned` table. The service performs the conditional update
(`UPDATE … WHERE id = %s AND record_version = %s`) and treats a zero row count as a stale-form
conflict; the trigger is the backstop for any path that forgets.

**Hard delete protection (§20)** is handled by `ON DELETE RESTRICT` on every FK pointing at
`satnet_path`, `spectrum_reservation`, `approval_decision`, `import_batch`, `audit_event` and used
master-data versions, plus the absence of any delete route in the application. Deactivation, not
deletion, is the retirement mechanism.

---

## 8. Transaction boundaries

### 8.1 The §15.6 boundary

One `READ COMMITTED` transaction, committing or rolling back as a unit:

```text
BEGIN
  1. policy.require(user, action, object)            -- authorisation
  2. optimistic-lock check on the Satnet Path        -- §15.5
  3. calculations.compute(...)                       -- pure, no I/O
  4. INSERT/UPDATE satnet_path                       -- derived values written by the service
  5. close superseded reservations (revision only)   -- BEFORE step 6 (A-14)
  6. UPSERT reservation[canonical leg]               -- may raise exclusion_violation
  7. UPSERT reservation[translated leg]              -- may raise exclusion_violation
  8. INSERT approval_decision                        -- when the action is an approval
  9. UPSERT hardware_reservation                     -- when hardware is exclusive
 10. INSERT audit_event                              -- always, success or failure
COMMIT
```

Step ordering is load-bearing. Steps 6 and 7 must follow step 5 because the constraint is `IMMEDIATE`;
step 10 is inside the transaction for success and re-emitted in a **separate** transaction on failure,
so that a rolled-back attempt still leaves an audit trail (§18 requires failures to be audited).

`READ COMMITTED` is sufficient: the exclusion constraint, not a snapshot, provides the serialisation
guarantee for overlap. No `SERIALIZABLE` isolation and no table-level locking is used.

### 8.2 Concurrency behaviour (§8.3, §26.15)

Two transactions proposing overlapping ranges:

```text
T1: INSERT reservation [A]     -> index entry taken, T1 holds it uncommitted
T2: INSERT reservation [A']    -> overlaps T1's uncommitted row
                               -> T2 BLOCKS on T1's transaction id
T1: COMMIT                     -> succeeds
T2: unblocks -> raises 23P01 exclusion_violation -> rolls back
```

Exactly one commit, with no application-level locking. This is the mechanism `tests/db/
test_concurrency.py` asserts, using two real connections and
`pytest.mark.django_db(transaction=True)`.

An optional contention reducer — `pg_advisory_xact_lock` keyed on
`(beam_id, frequency_window_id, leg, polarization)` to serialise placement within one scope — is
**not** in the MVP. It would turn a failed transaction into a wait, but it is an optimisation, and the
constraint must remain the authority either way.

### 8.3 Turning a constraint violation into the §9.5 message

`exclusion_violation` tells you that a conflict happened, not with what. The service therefore
catches it and runs a **second, read-only query** to build the message:

```python
try:
    with transaction.atomic():
        _write(...)
except IntegrityError as exc:
    if getattr(exc.__cause__, "sqlstate", None) != EXCLUSION_VIOLATION:
        raise
    conflicts = spectrum.selectors.find_conflicts(proposal)   # same key, && ranges
    raise SpectrumConflict(proposal=proposal, conflicts=conflicts)
```

`SpectrumConflict` carries everything §9.5 requires: violated rule, Beam, Frequency Window, proposed
range, conflicting Satnet and Satnet Path, overlap amount, validity overlap, and suggested free gaps
from the gap engine. The pre-check in the wizard uses the *same* `find_conflicts` selector, so the
message a user sees before saving and the message they see after a lost race are produced by one code
path.

### 8.4 Import commit (§17.1)

The batch policy chooses the boundary:

| Policy | Boundary |
|---|---|
| `ALL_OR_NOTHING` | one transaction for the whole batch; any error rolls everything back |
| `ROW_BY_ROW` | one transaction per row, each with a savepoint; failures are recorded as `ERROR`/`CONFLICT` rows and the batch continues |

Both paths call the **same** `satnet_paths.services.create()` as the UI (§11, §17.1), so imported rows
receive identical calculation, validation, reservation and audit behaviour. Commit re-verifies the
uploaded file's SHA-256 against the dry-run record before touching production data.

---

## 9. What the database deliberately does **not** enforce

Stated explicitly so nobody assumes protection that is not there:

| Rule | Why not a constraint | Where it is enforced |
|---|---|---|
| Allocated range fits inside its Frequency Window | Cross-table (`reservation` → `frequency_window` range). A trigger could do it, but it would fire on every insert and duplicate a service check that must produce a good message anyway. | Service + a nightly consistency job that reports violations |
| Payload translation correctness | Requires the calculation engine | Service (`calculations.translation`) |
| Equipment RF/IF containment | Requires the profile's algebra | Service (`calculations.conversion`) |
| Polarization mapping is in the allowed set | Cross-table to `payload_polarization_mapping` | Service + Beam validation |
| Satnet period inside Beam period | Cross-table | Service + consistency job |
| Minimum edge guard against Window edges | **OQ-34** — whether this is part of the range or a separate rule is unconfirmed | Service, currently a blocking validation |
| Tuning raster alignment | **OQ-31** — raster values unknown | Service, informational until confirmed |

The consistency job runs as a management command, writes its findings to the audit log, and is
exposed on an operations page. It exists because §20's constraint list is a floor, not a ceiling, and
cross-table engineering rules genuinely cannot be CHECK constraints.

---

## 10. Constraint test coverage (§25)

Every constraint in this document has a test that **attempts the violation via the ORM and expects an
`IntegrityError`**, not a test that merely inspects the migration:

```text
tests/db/
├── test_exclusion_same_scope.py       # same beam/window/leg/pol/time, overlapping RF -> blocked
├── test_exclusion_cross_satnet.py     # different Satnets, same Beam -> blocked          (§26.13)
├── test_exclusion_same_satnet.py      # same Satnet -> blocked
├── test_exclusion_adjacent.py         # half-open adjacency with guards -> allowed       (A-11)
├── test_exclusion_polarization.py     # different Window/polarization -> allowed
├── test_exclusion_time.py             # disjoint validity -> allowed
├── test_exclusion_translated_side.py  # only the translated side overlaps -> blocked     (§8.2)
├── test_exclusion_status_policy.py    # DRAFT/CANCELLED do not reserve; SUSPENDED follows the setting
├── test_exclusion_fixed_reserve.py    # a fixed reserve blocks a path                    (A-13)
├── test_concurrency.py                # two connections, exactly one commit              (§26.15)
├── test_composite_fks.py              # a mismatched denormalised key column is rejected
├── test_checks.py                     # one case per CHECK in §4
├── test_version_overlap.py            # two active master-data versions -> blocked
├── test_audit_immutable.py            # UPDATE and DELETE on audit_event both raise
├── test_record_version_trigger.py     # non-increasing record_version -> raise
└── test_extensions_present.py         # btree_gist present; /health/ready reflects it
```

These run against real PostgreSQL. There is no SQLite fallback anywhere in the project — none of this
exists there.
