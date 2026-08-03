# 05 — Vertical Slice Implementation Plan

**Derived from:** Root Specification §23 (Roadmap), §27 (Required Delivery Format).
**Governing rule (§27):** *"Do not implement the entire model before delivering working UI. Prefer
tested vertical slices that exercise presentation, service, domain, and persistence layers together."*

---

## 1. What counts as a slice

A slice is not merged until all seven hold:

1. It touches **all four layers** — a template a user can reach, a service function, domain logic
   where applicable, and a migration.
2. It ships its **own tests**, including at least one negative/permission test.
3. It updates **seed data** so `make up` still produces a working demo.
4. Its **ADRs** are written in the slice that makes the decision, not retro-fitted.
5. `make check` passes: Ruff, mypy, import-linter, terminology and English-only tests.
6. It carries a **slice report** in `docs/slices/NN-<name>.md` using the §27 seven-field format.
7. It invents **no RF value**. A slice that needs one either takes it from confirmed data or ships
   the container empty and cites the `OPEN QUESTION`.

Every slice report uses this shape:

```markdown
## Goal
## Files created or changed
## Database impact
## Security and permission impact
## Tests added
## Acceptance criteria covered        (§26.n)
## Remaining open questions           (OQ-nn)
```

---

## 2. Dependency graph

```text
S0  RF confirmation package  ─────────────────────┐ (parallel, non-blocking for S1–S7)
                                                  │
S1  Foundation ──► S2 Identity/Scope/Audit ──► S3 Specification Dictionary
                            │                     │
                            ▼                     ▼
                   S4 Independent inventory ──► S5 Dependent inventory + versioning
                                                  │
                            S6 Calculation core ◄─┘
                                   │
                            S7 Translation + IF + equipment matching
                                   │
                            S8 Beam Builder ──► S9 Reservations + constraints + gaps   ◄── needs OQ-25/26/27
                                                       │
                                              S10 Satnets ──► S11 Satnet Path wizard
                                                                     │
                                              S12 Lifecycle + approvals + revisions
                                                                     │
                                    S13 Tables/dashboard ──► S14 Export ──► S15 Import
                                                                     │
                                                              S16 Audit UI
                                                                     │
                                                     S17 Hardening ──► S18 Pilot + acceptance
```

**S6 and S7 can run in parallel with S4/S5** — the calculation engine is pure and depends on
dataclasses, not on models. This is deliberate: it lets the highest-risk logic be built and
property-tested before any inventory data exists.

**S9 is the gate.** `OQ-25` (cross-Beam reuse), `OQ-26` (remote-side equipment) and `OQ-27` (Beam
sub-ranges) all affect the exclusion-constraint key or the Satnet Path schema. Answering them after
S9 means migrating a constraint-bearing table with live data.

---

## 3. The slices

### S0 — RF Domain Confirmation Package *(Phase 0 — no code)*

**Goal.** Turn §24 and the new questions in `00-assumptions-and-open-questions.md` into something RF
engineering can actually fill in, and get §3.1 answered before S9.
**Deliverables.** One intake workbook per subject (Frequency Windows, Payload translations,
polarization mappings, equipment profiles, guard policies, golden FWD/RTN examples), each with the
exact columns the eventual import expects; a decision sheet for the policy questions (`OQ-05`,
`OQ-08`, `OQ-11`, `OQ-13`, `OQ-23`); a one-page briefing on `OQ-25`/`OQ-26`/`OQ-27` explaining the
schema consequence of each answer.
**Not delivered.** Any value. The workbooks ship empty.
**Open questions.** All of them — this slice exists to close them.

---

### S1 — Foundation and Health *(Phase 1)*

**Goal.** `make up` produces a running, English, authenticated-shell application against PostgreSQL 16
with `btree_gist`, and CI is green.
**Files.** `pyproject.toml`, `uv.lock`, `compose.yaml`, `docker/`, `config/settings/{base,local,test,production}.py`, `config/urls.py`, `operations/views.py`, `templates/base.html`, `templates/partials/nav.html`, vendored `static/`, `Makefile`, `.github/workflows/ci.yml`, `tests/ui/test_terminology.py`, `tests/ui/test_language.py`.
**Database.** Initial migration creating `btree_gist`, `citext`, `pgcrypto`. No domain tables.
**Security.** Secure/HttpOnly/SameSite cookies, CSRF, safe error pages, secrets from environment only, `/health/live` and `/health/ready` unauthenticated but disclosing nothing beyond status (§21).
**Tests.** Health endpoints (live vs ready when the DB is down), extension presence, terminology and English-only sweeps, `make check` in CI.
**Acceptance.** §26.1 (foundation), §26.18 (README/ADR scaffolding).
**ADRs.** 0001 modular monolith, 0002 Django + HTMX.
**Open questions.** OQ-17, OQ-23.

---

### S2 — Identity, Roles, Scopes and Audit Skeleton *(Phase 1)*

**Goal.** Four demo roles can log in, the navigation reflects capability, scope grants exist and are
enforced by an empty-but-real scoped queryset, and every auth event is audited.
**Files.** `accounts/{models,policy,managers,services,forms,views,urls}.py`, `audit/{models,services}.py` + immutability migration, `templates/accounts/`, `templates/administration/`, seed data migration for groups and demo users.
**Database.** `user`, `user_role`, `user_beam_scope`, `user_hub_scope`, `user_gateway_scope`, `login_attempt`, `audit_event` + the append-only trigger and audit indexes.
**Security.** This *is* the security slice: `policy.require` choke point, denial auditing, login throttle and temporary lockout, password validators, 404-not-403 for out-of-scope objects.
**Tests.** `tests/permissions/test_matrix.py` skeleton, `test_audit_immutable.py`, `test_denial_is_audited.py`, login throttle, lockout, `test_all_urls_declare_perms.py`.
**Acceptance.** §26.16 (partially — the enforcement mechanism), §26.17.
**ADRs.** 0013 append-only audit.
**Open questions.** OQ-11, OQ-15, OQ-16, OQ-30.

---

### S3 — Specification Dictionary and the Info Popover *(Phase 2)*

**Goal.** Admin manages display metadata for specification codes from one screen, and every place a
code appears anywhere in the product renders through one component.
**Files.** `specifications/{models,registry,services,forms,views,urls}.py`, `specifications/templatetags/spec_tags.py`, `templates/specifications/`, `templates/partials/spec_info_button.html`.
**Database.** `specification_category`, `specification_definition`; data migration seeding the codes named in §2 with **empty** descriptions where they encode engineering meaning that is not yet confirmed.
**Security.** `change_specificationdefinition` is admin-only; `code` is read-only for registry-backed rows (**A-20**).
**Tests.** Admin can edit metadata and non-admins cannot (§25); popover is reachable and operable by keyboard, not hover-only (§26.3); a template sweep asserting no hard-coded specification description exists anywhere else (§2).
**Acceptance.** §26.2, §26.3.
**ADRs.** 0011 Specification Dictionary.
**Open questions.** None new.

---

### S4 — Independent Inventory *(Phase 2)*

**Goal.** Satellites, Bands, Gateways, Hubs and Equipment Profiles are fully manageable, with the
Inventory section visibly split into Independent and Dependent groups, and dependency summaries that
block invalid deactivation.
**Files.** `inventory/` models, services, selectors, forms, views, urls, templates; `templates/inventory/_dependency_summary.html`.
**Database.** `satellite`, `band`, `band_polarization`, `gateway`, `hub`, `equipment_profile`; the `UNIQUE (id, gateway_id)` on `hub` that a later composite FK needs; `ON DELETE RESTRICT` throughout.
**Security.** Read for all authenticated roles; write admin-only. Deactivation refused while dependents exist.
**Tests.** Dependency-summary counts, deactivation blocked with dependents, gateway/hub separation preserved (§3.1), equipment conversion-vs-sideband CHECK.
**Acceptance.** §26.4.
**ADRs.** 0003 integer Hz and Decimal roll-off.
**Open questions.** OQ-04, OQ-14, OQ-31 (containers ship; values do not).

---

### S5 — Dependent Inventory and Master-Data Versioning *(Phase 2)*

**Goal.** Frequency Windows and Payload Paths exist as *versioned* master data, and a window in
operational use can only be changed by creating a new version.
**Files.** `inventory/models/{frequency_window,payload_path,guard_policy}.py`, `inventory/services/versioning.py`, templates for version history and diff.
**Database.** `frequency_window`, `payload_path`, `payload_polarization_mapping`, `guard_policy`; the version-overlap exclusion constraints (§04.3.3); the composite FKs pinning payload-path window sides (§04.3.2); `UNIQUE (id, side, polarization)` on `frequency_window`.
**Security.** Admin-only write; version creation audited with before/after.
**Tests.** Two active versions of one group rejected; a payload path whose window sides contradict its direction rejected **by the database**; retroactive overwrite of an in-use window refused.
**Acceptance.** §26.4.
**ADRs.** 0012 master-data versioning.
**Open questions.** OQ-01, OQ-02, OQ-03, OQ-07, OQ-35.

---

### S6 — Calculation Engine: Bandwidth, Edges, Guards *(Phase 4, pulled early)*

**Goal.** One engine, no formulas anywhere else, with an Engineering Preview page that lets a user
exercise it end-to-end before any Beam exists.
**Files.** `calculations/{units,ranges,bandwidth,types,validation}.py`, `calculations/views.py` (preview sandbox), `templates/calculations/preview.html`.
**Database.** None — the engine is pure.
**Security.** Preview is read-only and available to any authenticated user; it saves nothing.
**Tests.** `tests/domain/` — symbol-rate ↔ occupied-bandwidth round-trips, roll-off handling, guard resolution order, edge arithmetic, rounding policy, and Hypothesis properties: width preservation, half-open invariants, `occupied ⊆ allocated`, monotonicity.
**Acceptance.** §26.10 (partially), §26.16 (calculated values are engine-owned).
**ADRs.** 0008 half-open ranges, 0010 central calculation engine, 0016 guard policy hierarchy.
**Open questions.** OQ-05, OQ-06, OQ-29 (the rounding policy needs sign-off; the code documents it in one place so a change is one constant).

---

### S7 — Translation, IF Conversion, Equipment Matching *(Phase 4)*

**Goal.** Given a canonical-side allocation, the engine produces the translated-side allocation and
the L-band IF range, and picks or ranks compatible equipment profiles.
**Files.** `calculations/{translation,conversion}.py`, extended `validation.py`, extended preview page showing both sides.
**Database.** None.
**Security.** Unchanged.
**Tests.** Non-inverting and inverting translation, half-open behaviour through inversion (**A-10**) as a Hypothesis round-trip, `RF = LO ± IF` and `IF = |RF − LO|` with sideband disambiguation, RF/IF containment, profile ranking determinism. `tests/domain/golden/` is wired up and **empty pending OQ-22**, with a test that fails loudly if the directory is still empty at Phase 9.
**Acceptance.** §26.10, §26.12 (calculation half).
**ADRs.** 0006 two-sided reservations.
**Open questions.** OQ-02, OQ-04, OQ-22, OQ-26.

---

### S8 — Beam and Beam Builder *(Phase 3)*

**Goal.** An Admin builds a Beam through the guided wizard, sees the FWD and RTN chain diagrams and a
preview, and cannot activate it while an enabled direction is invalid.
**Files.** `beams/{models,services,selectors,policy,forms,views,urls}.py`, `beams/validation.py`, `templates/beams/builder/step_{1..5}.html`, chain-diagram partial.
**Database.** `beam`, `beam_direction_config`, `beam_direction_equipment_profile`, `beam_validation_result`.
**Security.** Beam engineering is admin-only; Operators cannot reach the builder URLs (tested by direct POST). Beam **view** becomes scope-filtered for non-admins.
**Tests.** Activation blocked on invalid configuration (§26.6); window/payload-path identity rule (**A-06**); polarization mapping must be in the allowed set; explicitly disabled direction is permitted and shown; operator cannot edit Beam engineering data (§25).
**Acceptance.** §26.6, §26.7.
**ADRs.** 0004 Beam root pool and Satnet child model.
**Open questions.** OQ-01, OQ-03, OQ-27, OQ-28, OQ-33.

---

### S9 — Spectrum Reservations, Constraints and the Gap Engine *(Phase 4)* — **gate slice**

**Goal.** The enforcement layer exists and is provably correct before anything can create an
allocation: reservation table, exclusion constraint, gap engine, and a read-only Spectrum view over a
Beam.
**Files.** `spectrum/{models,services,selectors,views,urls}.py`, `calculations/placement.py`, `templates/spectrum/`, ECharts spectrum map (vendored).
**Database.** `spectrum_reservation` with generated range columns, the `excl_reservation_overlap`
constraint, all composite FKs, all CHECKs of §04.4, and the GiST indexes.
**Security.** No write route exists for reservations, for any role (§13.11). The spectrum view is
scope-filtered.
**Tests.** The whole of `tests/db/` including `test_concurrency.py` (two connections, exactly one commit) and `test_exclusion_*`; gap-engine tests for free intervals, widths, nearest allocations, total free bandwidth, largest gap and utilisation.
**Acceptance.** §26.11, §26.14, §26.15.
**ADRs.** 0007 PostgreSQL exclusion constraints, 0009 calculated free capacity, 0017 suspended reservation policy.
**Open questions.** **OQ-25, OQ-26, OQ-27 must be answered before this slice starts.** Also OQ-08, OQ-24, OQ-34.

---

### S10 — Satnets *(Phase 5)*

**Goal.** An Operator creates and manages Satnets under authorised Beams and sees a live capacity
summary computed from the reservation table.
**Files.** `satnets/` full module, `templates/satnets/`.
**Database.** `satnet` + `UNIQUE (id, beam_id)` and the composite FK to `hub (id, gateway_id)`.
**Security.** The first real scope enforcement on a write path: Beam **and** Hub must both be in scope (**A-17**). Direct-POST tests for out-of-scope creation.
**Tests.** §25's "Operator can create Satnet only under authorized Beam"; Hub-grant-missing refusal; Satnet cannot outlive its Beam; inactive Satnet refuses new Paths; capacity summary matches a hand-computed fixture.
**Acceptance.** §26.8.
**Open questions.** OQ-21, OQ-30.

---

### S11 — Guided Satnet Path Creation *(Phase 5)*

**Goal.** The §9 workflow, end to end: context → capacity request → find spectrum (map, gaps,
Auto-place, manual centre) → live engineering preview → validated save producing two reservations.
**Files.** `satnet_paths/` full module, wizard steps as HTMX fragments, `templates/satnet_paths/wizard/`, `templates/satnet_paths/_validation_summary.html`.
**Database.** `satnet_path` with all range and CHECK constraints of §04.4; reservations written by the service inside the §15.6 transaction.
**Security.** Derived fields are unbindable for every role (§26.16); Auto-place proposes and never saves (§9.3); the server repeats every check on save (§9.5).
**Tests.** Both input modes with mutual exclusivity (§9.2); Auto-place ranking and determinism; the §9.5 blocking message content (rule, Beam, Window, proposed range, conflicting Satnet Path, overlap amount, validity overlap, suggested gaps); two reservations created atomically; rollback leaves no partial state; a translated-side-only conflict is blocked (§8.2).
**Acceptance.** §26.9, §26.10, §26.11, §26.12, §26.13, §26.16.
**ADRs.** 0005 Satnet Path terminology.
**Open questions.** OQ-09, OQ-10, OQ-31, OQ-32.

---

### S12 — Lifecycle, Approvals and Revisions *(Phase 6)*

**Goal.** The §15.2 transition graph, second-person approval, `ON_AIR` revision without overwrite, and
optimistic locking with a field-level difference view.
**Files.** `approvals/` module, `satnet_paths/services/lifecycle.py`, `templates/approvals/`, `templates/satnet_paths/_stale_form_diff.html`.
**Database.** `approval_decision`; `record_version` triggers active on all editable tables.
**Security.** Transition capabilities per role (§03.2.1); `REQUIRE_SEPARATE_APPROVER`; an Approver attempting an overlapping approval still hits the constraint (§25).
**Tests.** Every legal transition and a representative illegal one; approver-cannot-bypass; stale submission rejected with a diff (§15.5); revision closes the old period **before** opening the new one and both live in one transaction; suspended-reservation policy honoured in both settings.
**Acceptance.** §26.14 (approver path), §26.17.
**ADRs.** 0014 On Air revisions.
**Open questions.** OQ-08, OQ-11, OQ-12.

---

### S13 — Satnet Path Table, Filters, Saved Views, Dashboard *(Phase 6)*

**Goal.** The §10.3 table with grouped columns and specification popovers, saved views, and the
dashboard.
**Files.** `reporting/` module, `templates/reporting/`, table column registry driven by the Specification Dictionary.
**Database.** `saved_view`; the list indexes of §04.6.
**Security.** Saved views are per-user; shared views require an explicit flag. All listings scope-filtered.
**Tests.** Filter/sort correctness, column selection persistence, no N+1 on the default view, dashboard cards match selector output.
**Acceptance.** §26.11.
**Open questions.** OQ-15, OQ-23.

---

### S14 — Export *(Phase 7)*

**Goal.** Normalized export first, then legacy-style export.
**Files.** `imports_exports/export/{normalized,legacy}.py`, `templates/imports_exports/`.
**Database.** None beyond an export audit event.
**Security.** Formula-injection protection on every written cell (§21.12); exports scope-filtered at the queryset; filter parameters recorded in the workbook.
**Tests.** A cell beginning `=`/`+`/`-`/`@` is neutralised; scope filtering holds for an Observer; the Data Dictionary sheet matches the Specification Dictionary; round-trip of UUIDs.
**Acceptance.** §26.17, §26.19.
**Open questions.** OQ-18 — the legacy export is deliberately sized only after a real sample workbook is supplied; until then, only the normalized export ships.

---

### S15 — Import: Dry-Run and Commit *(Phase 7)*

**Goal.** Two-stage import that recalculates everything through the same services and never trusts
Excel-calculated values.
**Files.** `imports_exports/import/{parse,normalize,map,classify,commit}.py`, review UI templates.
**Database.** `import_batch`, `import_row`, `import_mapping`.
**Security.** Admin-only; upload validation, no macro execution, SHA-256 verified between dry-run and commit; dry-run touches no production data.
**Tests.** All seven row classifications; free-capacity rows ignored, never imported as Satnet Paths (§17.1); imported conflicts reported and not activated; stable UUIDs honoured; idempotent re-commit; both batch policies; every import action audited.
**Acceptance.** §26.17, §26.19.
**ADRs.** 0015 import dry-run.
**Open questions.** OQ-18.

---

### S16 — Audit UI *(Phase 7)*

**Goal.** Object history, actor/time search, and field-level before/after differences.
**Files.** `audit/{selectors,views,urls}.py`, `templates/audit/`.
**Database.** GIN indexes on the JSONB payloads.
**Security.** `view_all_auditevent` for Admin; others see their own actions; no edit or delete route.
**Tests.** Diff rendering for a representative change of each entity; search by actor/time/object; UPDATE/DELETE still rejected at the database.
**Acceptance.** §26.17, §26.19.
**Open questions.** OQ-15.

---

### S17 — Production Hardening, Backup and Restore *(Phase 8)*

**Goal.** The §21/§22 requirements are implemented, tested and documented, not merely described.
**Files.** `docker/nginx/`, production compose, `docs/runbooks/{deploy,backup,restore,incident}.md`, monitoring configuration.
**Database.** Backup and restore tooling; a restore-verification management command.
**Security.** TLS, HSTS, rate limits, MFA for admin accounts, dependency and image scanning in CI, PostgreSQL unreachable from outside the compose network, only 443 published.
**Tests.** A restore drill executed against a real dump verifying login, Beam detail, Satnet Path detail, latest Audit Event, row counts and export (§22.4); load test on the gap engine and the spectrum view; a smoke suite used by the release flow.
**Acceptance.** §26.19.
**Open questions.** OQ-19, OQ-17.

---

### S18 — Pilot, Cutover and Final Acceptance *(Phase 9)*

**Goal.** Load validated operational data, run the controlled comparison against the spreadsheets,
resolve differences, and produce the acceptance checklist with evidence.
**Deliverables.** `docs/acceptance-checklist.md` — one row per §26 criterion with pass/fail, the test
or screenshot that evidences it, and the commit that delivered it. Any criterion still failing is
reported as failing, not softened.
**Gate.** §26.20 — every unresolved RF rule is a recorded `OPEN QUESTION` and no value was invented.
The `OPEN QUESTION` register must be empty of §3.1 items before the application becomes the source of
truth.

---

## 4. Sequencing rationale

Three orderings differ from the specification's phase list, deliberately:

| Change | Reason |
|---|---|
| Calculation engine (S6, S7) built before the Beam Builder (S8), rather than after it as Phase 4 suggests | The engine is pure and has no dependencies. Building it first means the Beam Builder's validation step (§5.4.4) can call real logic rather than a stub, and the riskiest code gets the longest exposure to property tests. |
| Reservations and constraints (S9) before Satnet Paths (S11), splitting Phase 4/5 | The database is the final authority (§8.3). Building the enforcement layer *before* the feature that depends on it means the concurrency test exists before the first reservation is ever written, instead of being added afterwards to a system already assumed correct. |
| Export (S14) before Import (S15), where §23 Phase 7 lists import first | Export forces the normalized row shape to be settled, and that shape is exactly what the importer must accept. Building the importer first would mean designing the same contract twice. |

## 5. What is *not* being built

Restating so scope stays honest: no `Interference Domain` or replacement reuse-domain object (§4), no
`Carrier` entity or label (§7), no stored free-capacity table (§16), no microservices, Kubernetes,
message broker, GraphQL, SPA, Redis, Celery or CDN dependency (§19.4), and no NMS integration
(**OQ-20**, out of MVP scope).
