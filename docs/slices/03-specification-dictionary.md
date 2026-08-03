# Slice S3 — Specification Dictionary and the Info Popover

**Phase:** 2 (Specification Dictionary and Inventory)
**Report format:** Root Specification §27

---

## Goal

An administrator manages display metadata for every technical field from one screen, and
every place a specification code appears in the product renders through a single
accessible component — so a description is written once and shown everywhere.

## Files created or changed

**specifications** — `registry.py`, `models.py`, `constants.py`, `selectors.py`,
`services.py`, `forms.py`, `views.py`, `urls.py`, `apps.py`,
`templatetags/spec_tags.py`, `management/commands/check_specifications.py`,
`migrations/{0001_initial, 0002_code_immutable, 0003_seed_dictionary, _seed_helper}.py`

**Interface** — `templates/partials/spec_info_button.html` (the single component),
`templates/specifications/{list,detail,edit}.html`, `static/js/spec-popover.js`,
specification styles in `static/css/app.css`, navigation entry

**accounts** — `seeding.py` extracted from the role migration,
`migrations/0003_reseed_specification_capabilities.py`, two capabilities added to the
matrix

**Tooling** — `pytest-playwright` added, `tests/ui/conftest.py` for browser discovery,
`browser` marker, import contracts and mypy scoping extended

**Documentation** — `docs/adr/0011-specification-dictionary.md`, this report

## Database impact

| Table | Notes |
|---|---|
| `specification_category` | Admin-managed grouping. A table rather than an enum because §2 lists Category alongside display order as admin metadata. |
| `specification_definition` | The §2 field list in full, plus `is_system_managed`, `record_version` and two indexes including a partial one for table-visible rows. |

Three migrations: schema, the code-immutability trigger, and an idempotent seed of the
thirteen codes. The seed is **non-destructive** — it creates missing rows and never
overwrites existing ones, because an administrator's edited description must survive the
next deploy.

A fourth migration in `accounts` re-applies the capability matrix. Permission changes
reach production as a reviewed migration rather than as a deployment side effect, which
suits §22.3's separate migration-review step.

## Security and permission impact

- **Read for every role, edit for Admin only.** An Operator needs to look up what a code
  means; only an administrator changes what it says (§12, §26.2).
- **Code immutability in three layers.** The form omits `code` from its field list — not
  merely disabled, so a crafted POST carrying it is ignored; the service rejects any field
  outside an explicit allow-list; and a database trigger refuses to rename a
  system-managed row. The third layer exists because a rename would silently detach a code
  from the calculation engine that refers to it by name, and the failure would surface
  much later as a missing value rather than an error.
- **Semantic fields are unbindable.** `category`, `data_type`, `direction_applicability`
  and `is_calculated` are absent from the form: they are classification the calculation
  engine relies on, not presentation.
- **Every edit is audited** with field-level before and after values, and denials are
  audited by the S2 choke point. Authorisation runs before the transaction opens, per
  ADR-0013.
- **Optimistic locking** on edits (§15.5): a stale submission is rejected with a 409 and
  the current values, rather than silently overwriting a concurrent edit.

## Tests added

200 tests total, up from 121.

| File | Covers |
|---|---|
| `tests/specifications/test_dictionary.py` | Registry consistency; all eleven codes named in §2 present; **no RF value invented**; read permitted for every role and denied to anonymous; admin can edit and non-admins cannot by direct POST; code rejected by form, by service and by the database; a non-system code may still be renamed; edits audited with before/after; stale edits rejected; missing code returns None rather than raising; the cache primes in one query |
| `tests/ui/test_spec_popover_accessibility.py` | **Eight browser-driven tests** in real Chromium: the button is reachable by keyboard, Enter opens, Space opens, Escape closes and returns focus, the popover shows name/code/unit/calculation, hover alone does *not* open it, only one is open at a time, clicking outside dismisses |
| `tests/ui/test_no_hardcoded_descriptions.py` | Seeded wording appears nowhere outside the registry and the one component; no template prints a §2 code without the component; the sweep is non-vacuous |
| `tests/test_imports.py` | Every application module imports cleanly — see below |

The accessibility tests are browser-driven deliberately. Asserting that the HTML contains
`aria-expanded` proves the attribute exists; it does not prove that pressing Enter opens
anything or that Escape restores focus. §25 asks whether the popover *is keyboard
accessible*, so that is what is tested. They skip cleanly where no Chromium is present.

### A trap I fell into twice

django-stubs declares `ListView`, `DetailView` and `ModelForm` as generic, so mypy asks
for a type parameter — but the real Django classes define no `__class_getitem__`, and the
subscript raises `TypeError` at import time. I wrote `ListView[User]` in S2 and
`ModelForm[SpecificationDefinition]` here: both type-checked perfectly and took the whole
application down, surfacing as dozens of unrelated failures because every view that
touches the URL configuration collapses together.

Rather than patch it a second time, the rule is now off for `*.views` and `*.forms` with
the reasoning recorded, and `tests/test_imports.py` imports every module so the failure
names the module and the cause instead of scattering.

### Two testing hazards found

**`django_db(transaction=True)` flushes migration-seeded data.** The first browser test
passed and the second failed with a permission error, because the flush after test one had
removed the role groups. Any later transactional test would have had `groups.set()`
silently assign nothing — an authorization test passing for entirely the wrong reason.
Fixed with explicit `seeded_roles` and `seeded_dictionary` fixtures, now also used by the
S2 transactional test that had the same latent problem.

**A popover overlays the row beneath it.** A mouse click aimed at the next information
button lands on the open popover. That is what a popover is for, so the behaviour stands;
the test drives the keyboard path instead, which is the requirement anyway.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.2 — admin manages display names, descriptions, units, help text, visibility and order from one central screen | **Met.** `/specifications/` is that screen; edits are audited and version-checked. |
| §26.3 — specification codes appear with a small accessible info button and popover | **Met and proven in a browser.** Keyboard operable, not hover-dependent, focus restored on dismiss. |
| §26.20 — unresolved RF rules recorded, never invented | **Enforced by test.** `test_no_rf_engineering_value_was_invented` fails if a calculation note is supplied for the codes whose rule is an open question. |
| §26.16 — calculated values read-only for operators | **Advanced**: `is_calculated` is recorded per specification and shown in the popover; enforcement on the values themselves is S11. |
| §26.18 | **Partial**: ADR-0011 added; runbooks remain S17. |

## Verification performed

```
pytest                                   200 passed (incl. 8 browser tests)
ruff check . / ruff format --check .     clean, 82 files
mypy config operations accounts audit specifications   no issues in 54 files
lint-imports                             4 contracts kept, 0 broken
manage.py migrate                        clean
manage.py check_specifications           13 specifications, none missing a description
```

Role sweep through the real HTTP stack:

```
admin     list=200 detail=200 edit=200
operator  list=200 detail=200 edit=403
observer  list=200 detail=200 edit=403
anonymous list=302 -> /accounts/login/?next=/specifications/

13 information buttons rendered, accessible names:
  "Symbol rate", "Roll-off factor", "Occupied bandwidth"
```

## What was deliberately not invented

Thirteen codes are seeded — the eleven named in §2 plus `LEFT_GUARD` and `RIGHT_GUARD`,
which §14.3 implies. Descriptions restate what the root specification itself states.

Three calculation notes are **deliberately empty**, and a test keeps them that way:

| Code | Why empty |
|---|---|
| `FWD_REMOTE_DL_CENTER_RF` | Payload translation method and constant per path — **OQ-02** |
| `RTN_HUB_DL_CENTER_RF` | Same |
| `L_BAND_CENTER_IF` | Depends on the equipment profile's LO and sideband — **OQ-04** |

They render as codes with a description but no formula, and are listed by
`check_specifications` so the gap stays visible. Filling them in is a data edit once
engineering answers, not a code change.

Display precision defaults to three decimal places in MHz, following the worked example in
§9.5 (`29,145.000–29,155.500 MHz`). That is a presentation default, adjustable per
specification without a deploy — which is exactly what the dictionary is for.

## Remaining open questions

Touched, not resolved: **OQ-02** and **OQ-04** as above; **OQ-23** (display time zone) —
the dictionary can hold a timestamp precision once a time zone is chosen.

Unchanged and still required before **S9**: **OQ-25**, **OQ-26**, **OQ-27**.

## Next slice

**S4 — Independent Inventory.** Satellites, Bands, Gateways, Hubs and Equipment Profiles,
with the Inventory section visibly split into Independent and Dependent groups and
dependency summaries that block invalid deactivation. It also lands the first real scope
resolvers, which is where **OQ-30** becomes concrete.
