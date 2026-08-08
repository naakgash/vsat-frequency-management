# Acceptance checklist — §26

**Generated for:** S18, Phase 9
**Verified by:** `tests/acceptance/test_acceptance_checklist.py`
**Gate:** `manage.py acceptance_gate`

---

## How to read this

One row per §26 criterion. Three columns carry the weight:

- **Status** — one of `Met`, `Met in code, unproven against reality`, `Partial` or `Blocked`.
  Nothing is softened. A criterion that is not met says so.
- **Evidence** — a test file that fails if the claim stops being true. Every path in this
  document is checked by `tests/acceptance/test_acceptance_checklist.py`, which fails if a file
  named here does not exist. A row with no evidence cannot claim `Met`.
- **Commit** — where it landed. Also checked; a commit named here must exist in history.

**`Met in code, unproven against reality` is the important status**, and it is not a euphemism.
It means the behaviour is implemented and tested against values this project invented for its
own fixtures, and that **no real RF engineering value has ever been through it**. The
calculation engine is the clearest case: every property holds, and whether it computes the right
answer for an actual transponder is **OQ-22**, which no amount of building can settle.

---

## The criteria

| § | Criterion | Status | Evidence | Commit |
|---|---|---|---|---|
| 26.1 | Foundation: one command starts the stack, health endpoints answer | **Met** | `tests/test_health.py`, `tests/test_container_startup.py` | `fe1d25a` |
| 26.2 | Every field description comes from the Specification Dictionary | **Met** | `tests/specifications/test_dictionary.py`, `tests/ui/test_no_hardcoded_descriptions.py` | `5d47a05`, `102c82a` |
| 26.3 | An information popover explains each field, accessibly | **Met** | `tests/ui/test_spec_popover_accessibility.py`, `tests/ui/test_no_hardcoded_descriptions.py` | `5d47a05` |
| 26.4 | Inventory is administered, versioned and never overwritten | **Met** | `tests/inventory/test_versioning.py`, `tests/inventory/test_dependencies.py` | `7905a63`, `42e2594` |
| 26.5 | Four roles, enforced in the backend rather than by hiding buttons | **Met** | `tests/permissions/test_matrix.py`, `tests/permissions/test_url_coverage.py` | `d5550ac` |
| 26.6 | The Beam is the root spectrum pool and its directions are validated | **Met** | `tests/beams/test_validation.py`, `tests/beams/test_spectrum_model.py` | `56e68f9`, `65059ae` |
| 26.7 | A Beam cannot be activated while an enabled direction is invalid | **Met** | `tests/beams/test_activation.py` | `56e68f9` |
| 26.8 | Overlapping allocations are impossible | **Met** | `tests/spectrum/test_exclusion.py`, `tests/spectrum/test_concurrency.py` | `cb99f89` |
| 26.9 | Guided creation: the operator is walked through a valid allocation | **Met** | `tests/satnet_paths/test_wizard.py` | `51c5826` |
| 26.10 | Derived values are calculated, not typed | **Met in code, unproven against reality** | `tests/domain/test_bandwidth.py`, `tests/domain/test_translation.py`, `tests/domain/test_conversion.py` | `8658f89`, `dfc8b58` |
| 26.11 | Free capacity and the spectrum picture are shown and are correct | **Met in code, unproven against reality** | `tests/spectrum/test_capacity.py`, `tests/domain/test_gaps.py`, `tests/reporting/test_table.py` | `cb99f89`, `102c82a` |
| 26.12 | Equipment RF/IF limits are respected | **Met in code, unproven against reality** | `tests/domain/test_conversion.py`, `tests/satnet_paths/test_wizard.py` | `dfc8b58`, `51c5826` |
| 26.13 | An allocation lives inside the intersection of its parents' periods | **Met** | `tests/satnets/test_containment.py` | `fae1a8c` |
| 26.14 | No one may bypass the overlap guarantee, including an administrator | **Met** | `tests/spectrum/test_exclusion.py`, `tests/satnet_paths/test_lifecycle.py`, `tests/approvals/test_approvals.py` | `cb99f89`, `d007878` |
| 26.15 | Master data is superseded, and history stays readable | **Met** | `tests/inventory/test_versioning.py`, `tests/satnet_paths/test_revisions.py` | `42e2594`, `d007878` |
| 26.16 | Every derived value is system-owned; a submitted one is ignored | **Met** | `tests/satnet_paths/test_allocation.py`, `tests/imports_exports/test_import.py` | `51c5826`, `b2ace0e` |
| 26.17 | Traceability: who did what, when, and to which record | **Met** | `tests/audit/test_immutability.py`, `tests/audit/test_audit_ui.py` | `d5550ac`, `fcf1384` |
| 26.18 | Deployment, operations and documentation | **Met** | `tests/operations/test_production_posture.py`, `tests/operations/test_backup_and_restore.py` | `fe1d25a`, `a10838f` |
| 26.19 | Import and export, with a data dictionary and a verified restore | **Partial** | `tests/imports_exports/test_export.py`, `tests/imports_exports/test_import.py`, `tests/operations/test_backup_and_restore.py` | `7693c79`, `b2ace0e`, `a10838f` |
| 26.20 | No RF value is invented; every unresolved rule is a recorded question | **Met as a discipline; the gate is open** | `tests/rf_confirmation/test_intake_templates.py`, `tests/acceptance/test_acceptance_checklist.py` | `d53c4dc` |

---

## The rows that are not simply "Met"

### §26.10, §26.11, §26.12 — *Met in code, unproven against reality*

The engine computes bandwidth, band edges, guards, payload translation, RF/IF conversion and
equipment matching. Every algebraic property is held by tests, including property-based ones:
translation round-trips, spectral inversion is its own inverse, guards never overlap the
occupied range, and free capacity plus allocated capacity is the whole assignment.

**None of that says the numbers are right for a real transponder.** The fixtures use frequencies
this project made up to satisfy constraints, and `tests/domain/golden/` — where a worked example
from an RF engineer would live — contains a README and no examples. §24 asks for one and is
explicit that it must come from a currently operational Satnet Path, calculated independently.
That is **OQ-22**, and it is the one criterion in this list that **cannot be closed by
building**: anything this implementation produces to check itself against proves only that it
agrees with itself.

`tests/domain/test_golden_examples.py` skips today and becomes a hard failure with
`VSAT_REQUIRE_GOLDEN_EXAMPLES=1`. Set that in CI the day the file arrives.

### §26.19 — *Partial*

Three of four halves are done and the fourth is blocked:

| | |
|---|---|
| Normalized export, with a Data Dictionary sheet | **Met** (S14) |
| Two-stage import of that shape, dry run and commit | **Met** (S15) |
| Restore verified by a drill the suite executes | **Met** (S17) |
| **Legacy-layout export and import** | **Blocked on OQ-18** |

§17.2 asks for an export matching the incumbent spreadsheet closely enough that today's users
can keep working, and S15 added the mirror requirement for reading that layout back. Neither is
implementable from the specification: both need the actual workbook — sheet names, column order,
merged headers, the unit each column is written in, and the conventions that have accumulated in
it. `imports_exports/export/legacy.py` raises with the list of what is missing rather than
shipping a plausible layout, because an approximately-right export is wrong in ways nobody
notices until the Phase 9 comparison blames the engine for the export's mistakes.

### §26.20 — *Met as a discipline; the gate is open*

The discipline held: **no RF value was invented anywhere in this build.** The inventory ships
empty, `docs/rf-confirmation/` is how the values are asked for, and the intake sheets are
generated from the models so they cannot drift from what the schema needs.

The gate is a different question, and it is open. `manage.py acceptance_gate` reads the §3.1
table out of the register and reports **nine outstanding RF engineering values** and **zero
golden worked examples**. Each is named in full below, because a reader looking for one of them
will search for its identifier and a run-on list like "OQ-01, 02, 03" answers nothing:

| | Outstanding value | Lands in |
|---|---|---|
| **OQ-01** | Official FWD and RTN Frequency Windows for every Satellite and Beam | `inventory.FrequencyWindow` |
| **OQ-02** | Exact satellite translation method / LO per Payload Path | `inventory.PayloadPath` |
| **OQ-03** | Allowed uplink/downlink polarization mappings | `inventory.PayloadPolarizationMapping` |
| **OQ-04** | BUC / BDC / LNB RF, IF and LO limits by site and model | `inventory.EquipmentProfile` |
| **OQ-06** | Default roll-off by platform | `inventory.RolloffOption` |
| **OQ-07** | Guard policy by Band, Window and platform | `inventory.GuardPolicy` |
| **OQ-14** | Circular and/or linear polarization in use | `inventory.Band` |
| **OQ-22** | A validated golden worked example from a real operational Satnet Path | `tests/domain/golden/` |
| **OQ-24** | Fixed reserved spectrum areas | `SpectrumReservation(kind=FIXED_RESERVE)` |

Until those arrive, the platform is correct and is not the record. `docs/rf-confirmation/` is
how each is asked for, and the intake sheets there are generated from the models so they cannot
drift from what the schema needs.

---

## What Phase 9 still requires, and why it is not in this repository

S18's plan has two halves. The checklist above is one of them. The other is:

> *Load validated operational data, run the controlled comparison against the spreadsheets,
> resolve differences.*

**None of that can be done here**, and simulating it would be worse than not doing it:

| Step | Needs | Why it cannot be approximated |
|---|---|---|
| Load validated operational data | The nine §3.1 answers | §26.20. A plausible-looking invented Frequency Window is indistinguishable from a real one once loaded, and everything computed from it inherits the invention |
| Compare against the spreadsheets | The incumbent workbook (**OQ-18**) | There is nothing to compare against. A comparison against a spreadsheet this project wrote would compare the platform with itself |
| Resolve differences | Both of the above, plus RF engineering | A difference cannot be adjudicated without somebody who knows which side is right |

What *is* ready for that day: the import reads a file and reports what it would do without
touching anything (S15), the export produces the platform's own shape for comparison (S14), the
audit trail records every step (S16), and the restore drill means a bad load can be undone
(S17). The pilot is a scheduling problem now, not an engineering one.

---

## Summary

| | |
|---|---|
| Criteria fully met | **15** of 20 |
| Met in code, unproven against reality | **3** (§26.10, §26.11, §26.12 — all OQ-22) |
| Partial | **1** (§26.19 — legacy layout, OQ-18) |
| Discipline met, gate open | **1** (§26.20 — nine RF values outstanding) |
| Criteria failing for want of implementation | **0** |

Every gap in this document is a **missing input**, not missing code. That distinction is the
point of the whole exercise: the build is finished and the platform is not yet the source of
truth, and those are two different sentences.
