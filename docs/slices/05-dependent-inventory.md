# Slice S5 — Dependent Inventory and Master-Data Versioning

**Phase:** 2 (Specification Dictionary and Inventory)
**Report format:** Root Specification §27

---

## Goal

Frequency Windows and Payload Paths exist as versioned master data, with the version-overlap
exclusion constraints and the composite foreign keys that pin a Payload Path's window sides
to its direction. Equipment Profile's version columns shipped in S4; this slice adds the
versioning service all three share, and the screens that make it usable.

## Files created or changed

**inventory** — `models.py` split into a `models/` package (`base.py`, `independent.py`,
`dependent.py`), `versioning.py` (new), `units.py` (new), `templatetags/rf.py` (new),
`constants.py`, `services.py`, `forms.py`, `views.py`, `urls.py`, `apps.py`,
`migrations/0002_dependent_inventory.py`, `migrations/0003_payload_path_composite_keys.py`

**accounts** — `migrations/0006_reseed_dependent_inventory_capabilities.py`

**Interface** — `templates/inventory/`: three list screens, three detail screens, the
version-history and supersede screens, and the `_version_panel`, `_index_entry` and
`_form_fields` partials

**Tests** — `tests/inventory/{test_versioning,test_dependent_constraints,test_dependent_views,test_units}.py`,
plus `factories.py`

**Documentation** — `docs/adr/0012-master-data-versioning.md`, an amendment to ADR-0003,
this report

**Tooling** — `Makefile` and `.github/workflows/ci.yml`: the `types` gate now covers every
application module (see below)

## Database impact

| Table | Notes |
|---|---|
| `guard_policy` | Named separation rules. **No policy is seeded** (**OQ-07**) |
| `frequency_window` | Allocatable spectrum per satellite, band and leg; versioned |
| `payload_path` | Uplink→downlink translation; versioned |
| `payload_polarization_mapping` | Ships empty (**OQ-03**) |

Constraints worth naming:

- **`excl_window_version_overlap`** and **`excl_path_version_overlap`** — two active
  versions of one logical record are impossible. Without this an allocation would have two
  different definitions of what it must fit inside, and nothing would say which wins. They
  index a **stored generated** `tstzrange` column rather than an application-maintained
  one; see ADR-0012.
- **`fk_path_uplink_window_side`** and **`fk_path_downlink_window_side`** — composite
  foreign keys on `(window_id, side)`, added by `RunSQL` because Django cannot model a
  composite FK. These are the point of the slice. `ck_path_direction_sides` alone would let
  a row claim `HUB_UPLINK` while pointing at a remote-downlink window: it would satisfy the
  CHECK and still be wrong. The composite key requires the `(window, side)` pair to exist as
  an actual window, which is what turns "a FWD path runs hub uplink to remote downlink" from
  a convention the application maintains into a fact the database enforces.
- **`ck_guard_mode_has_required_values`** — a policy carrying none of the values its own
  mode needs would resolve to a zero guard silently, which is the one failure mode a guard
  must not have.
- **`ck_path_windows_differ`**, **`ck_window_start_lt_end`**, four non-negativity CHECKs on
  guard widths, and the `uq_window_id_side` / `uq_window_id_side_polarization` unique
  constraints that the composite keys target.

`PROTECT` throughout. A window referenced by a payload path cannot be deleted, and there is
no delete route in the application at all.

### A missing migration dependency, caught by a fresh database

The exclusion constraints key on a `uuid` column, which needs `btree_gist`. No inventory
migration declared a dependency on `operations.0001_extensions`, so the ordering was
accidental: it worked in development because the extension was already installed, and
failed on the fresh test database with `data type uuid has no default operator class for
access method "gist"`. Both `0001_initial` and `0002_dependent_inventory` now declare it.

## Security and permission impact

- Read for every role, write for Admin only, unchanged from S4 and enforced by direct POST
  in the tests.
- **Superseding requires `manage_inventory`** and is audited as `MASTER_DATA_VERSIONED`
  with the full before and after, plus the change reason.
- **The version-history screen is readable by any role.** Knowing which definition an
  allocation was validated against is part of reading the allocation, not an administrative
  extra.
- **The denormalised side columns are never accepted from the request.**
  `PayloadPathForm.model_values` derives them from the chosen windows. Letting a caller
  supply them would let them assert the very thing the composite key exists to verify;
  `test_the_side_columns_are_derived_and_not_accepted_from_the_request` posts wrong values
  and checks they are ignored.

### A defect this slice found, affecting every edit since S4

`ModelForm._post_clean` writes the submitted values onto the instance it was handed. The
edit view then passed **that same object** to the service — so by the time the service ran,
the "current" instance already described the proposed change.

Two things broke as a result:

- The audit `before` snapshot recorded the *new* values as the old ones. An audit record
  that reports no change is worse than a missing one: it looks authoritative.
- The §13.6 retroactive-edit guard compared the submitted values against themselves, found
  nothing changed, and permitted an edit it should have refused.

The second symptom is how it was found — a test asserting the guard fires got a `302`
instead of a `409`. Fixed with `inventory.views.stored_copy`, which refetches the row before
handing it to a service; `test_an_edit_records_the_values_that_were_actually_replaced` pins
the audit behaviour directly, so the regression is caught even if the versioning guard is
ever relaxed.

### A gate that was not covering what it claimed

`make types` ran `mypy config operations` — the S1 module list, never widened. `accounts`,
`audit`, `specifications` and `inventory` had never been type-checked. Widening it produced
nine errors, all real and all in this slice's code, the most useful being that
`MasterDataVersioned` used `effective_from`, `effective_until` and `is_active` without
declaring any of them: it silently required a sibling mixin to supply them, and a model that
versioned without one would have failed at the first `supersede` rather than at import. It
now inherits `EffectiveDatedModel` and `DeactivatableModel` directly.
`makemigrations --check` confirms the change is schema-neutral.

## Tests added

373 tests total, up from 282. 91 new:

| File | Covers |
|---|---|
| `test_versioning.py` (16) | Supersede closes the predecessor and opens the successor; touching periods allowed, overlapping refused by the database; a successor cannot predate its predecessor; double-supersede refused; audit carries before and after; **engineering values of an in-use record cannot be edited in place, but its description can**; history and current-version helpers; all three versioned entities on the same machinery |
| `test_dependent_constraints.py` (15) | Half-open window ranges; one polarization per window (**A-04**); FWD and RTN side rules; **a lying side column refused by the composite foreign key**; a window used by a path cannot be deleted; each guard mode accepts a complete policy and refuses an incomplete one; **no dependent inventory is seeded** |
| `test_dependent_views.py` (42, incl. parametrisation) | Read for every role and sign-in required on all three lists; current-versions-only by default with an explicit *all versions* view; the index counts a versioned window once; direct-POST writes refused for non-admins and audited; form-level mirrors of the CHECKs; side columns derived not accepted; supersede over HTTP; the retroactive-edit refusal names the supported route; version screens 404 for an unversioned entity; the activation route still resolves alongside the new version routes |
| `test_units.py` (18) | MHz↔Hz round-trips; sub-Hz input refused rather than rounded; the classic float failure shown not to occur; Ka-band exceeds 32-bit; the `mhz`/`hz` filters including the em-dash for absent values; **the two display paths compared against each other** |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.4 — Inventory visibly divided into Independent and Dependent | **Met and extended.** Frequency Windows and Payload Paths are now real links; Beams remain named with the slice that delivers them. Guard Policies are listed as *independent* — they hold no foreign keys, so by §3's own test that is what they are. |
| §26.20 — no invented RF values | **Enforced by test.** `test_no_dependent_inventory_is_seeded` fails if anyone ships a plausible window, translation, mapping or guard value. |
| §26.15 — concurrency | **Advanced.** The version-overlap exclusion constraints are the first use of the close-then-open transaction discipline the reservation engine needs in S12 (**A-14**). |
| §26.17 — data traceable | **Advanced.** A supersede is audited with before and after; the version chain is itself a record of what changed and when. |
| §26.18 | **Partial**: ADR-0012 added, ADR-0003 amended; runbooks remain S17. |

## Verification performed

```
pytest                                   373 passed
pytest -m browser                        8 passed
ruff check . / ruff format --check .     clean
mypy (6 modules, widened this slice)     no issues in 72 source files
lint-imports                             4 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
manage.py migrate                        clean on a fresh database
```

On that fresh database: 58 constraints across the four inventory tables this slice touches,
including all three `excl_*_version_overlap` and both composite foreign keys; `guard_policy`,
`frequency_window` and `payload_path` all at **0 rows**. Role capabilities after the reseed
migration: admin 15, operator 10, approver 10, observer 9 — three new `view_*` capabilities,
granted to every role, matching the read-for-all/write-for-admin rule.

## What was deliberately not invented

**Every table this slice adds ships empty.** Not one window edge, translation constant,
polarization mapping or guard width is supplied:

| Table | Open question |
|---|---|
| `frequency_window` | **OQ-01** — which windows exist, and their edges |
| `payload_path` | **OQ-02** — translation method and constant per payload |
| `payload_polarization_mapping` | **OQ-03** — which polarization pairs are permitted |
| `guard_policy` | **OQ-07** — guard values by band, window and platform |

The shapes are here; the numbers come from RF engineering. A plausible Ka-band window with
a made-up 10 GHz translation would be indistinguishable from a confirmed one once loaded.

Two further restraints worth flagging:

- **`FrequencyWindow.contains` does not apply the edge guard.** Whether the minimum edge
  guard forms part of an allocated range or is a separate validation is **OQ-34**, and
  implementing containment one way would answer it by implication.
- **`GuardPolicy` is not in the specification's entity list**, but §13.6 gives a window a
  "default guard policy" and §13.9 gives a Satnet one, so the thing they both default to
  needs somewhere to live. §9.2 additionally lets an operator *"select or accept"* a policy,
  which means it must be selectable — a bare pair of numbers would not be. Only the shape is
  fixed; the values remain **OQ-07**.

## Remaining open questions

Touched, not resolved: **OQ-01**, **OQ-02**, **OQ-03**, **OQ-07**, **OQ-34**.

**Unchanged and now urgent — required before S9:**

- **OQ-25** — whether the same frequency may be reused across different Beams. This
  determines the key of the central spectrum-overlap exclusion constraint, and getting it
  wrong means rebuilding that constraint on a populated table. Highest-risk item outstanding.
- **OQ-26** — whether remote-side equipment is modelled.
- **OQ-27** — whether a Beam carries sub-ranges of a Frequency Window.

## Next slice

**S6 — The Calculation Engine.** Occupied and allocated bandwidth from symbol rate and
roll-off, the guard resolution this slice's `GuardPolicy` shapes describe, and RF↔IF
conversion through an Equipment Profile — all in `Decimal` and integer Hz, with the rounding
policy (**A-09**) applied in exactly one place. It is a pure module with no database access,
which is what makes it testable against property-based cases rather than examples.
