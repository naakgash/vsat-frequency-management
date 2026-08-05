# Slice S11a — Controlled hardware references, and UTC on the face of the value

**Phase:** 5
**Report format:** Root Specification §27

---

## Goal

Four answers arrived at once — **OQ-09**, **OQ-10**, **OQ-22** and **OQ-23** — and three of them
touch things S11 had shipped as provisional. This slice implements them.

Like S9a and S10a, it exists because an answer changed the schema rather than filled a table.
The numbering follows that precedent: **S12 is still the lifecycle slice** S11's report named.

## What the answers changed

**OQ-09 and OQ-10 went in opposite directions, and the register had them as one question.** It
recorded OQ-10 as *"Is Decimator an exclusive hardware resource? Same as OQ-09"*, with the same
provisional position for both: a generic `HardwareResource` / `HardwareReservation` pair,
shipped and unpopulated. Neither answer wants that.

* **GW ID is a shared reference.** It becomes a foreign key to a controlled `Gateway` record and
  it must **never** decide double-booking, *"because the actual contention boundary is the
  underlying RF chain, payload input, polarization, frequency range and active period"*.
* **A Decimator is genuinely allocatable.** Two tables, and an exclusion constraint saying the
  same Decimator may not hold two different active configurations over overlapping periods.

Building the generic pair would have been wrong twice at once: a false conflict every time two
Satnets shared a Gateway, and no conflict at all when one Decimator was configured two ways over
the same week. S11's decision to ship both as validated free text — rather than guess at
exclusivity — is what left room to implement each correctly.

**OQ-23 confirmed a decision S11 had already made and flagged.** `Beam.effective_from` defaults
to the current instant rather than to midnight, and the S11 report recorded that as a sharp edge
awaiting this answer. It is now the rule.

**OQ-22's answer is a specification for a test harness.** It names what an approved example must
contain, and half of that is beyond what the old harness could run.

## Files created or changed

**inventory** — `Decimator` and `DecimatorAssignment` in `models/dependent.py`,
`migrations/0005_decimators`

**satnet_paths** — `gw_id`/`decimator` free text replaced by `gateway` and
`decimator_assignment` foreign keys (`models.py`, `services.py`, `forms.py`),
`migrations/0003_controlled_hardware_references`

**operations** — `templatetags/utc_tags.py` (new)

**config** — the `TIME_ZONE` comment, which was a pointer to OQ-23 and is now the answer

**Interface** — thirteen templates now render timestamps through `|utc`; the Satnet Path detail
screen shows its GW ID and Decimator with a note on what neither of them means

**Tests** — `tests/inventory/test_decimator_assignments.py` (11),
`tests/satnet_paths/test_hardware_references.py` (8), `tests/ui/test_utc_display.py` (11),
`tests/domain/test_golden_scenarios.py` (new harness), extensions to
`tests/domain/test_golden_examples.py` and `tests/rf_confirmation/test_intake_templates.py`

**Documentation** — ADR-0021, ADR-0022, the register (**A-26**–**A-29**, **OQ-36**),
`docs/design/02` and `docs/design/04`, the golden-example README and template, this report

## Database impact

| Table | Notes |
|---|---|
| `decimator` | The physical unit at a Hub. Identity only, unique per Hub. |
| `decimator_assignment` | What that unit is configured to do, over a period. |
| `satnet_path` | `gw_id` and `decimator` dropped; `gateway_id` and `decimator_assignment_id` added, both nullable. |

```sql
ALTER TABLE decimator_assignment
  ADD CONSTRAINT excl_decimator_assignment_overlap
  EXCLUDE USING gist (decimator_id WITH =, active_period WITH &&)
  WHERE (is_active);
```

The `is_active` condition mirrors `excl_window_version_overlap`: a withdrawn configuration is a
record of what used to be true, and refusing to keep it would push the platform towards deleting
history to make room.

**No constraint was added for the Gateway, and that absence is the point.** A test asserts that
`SpectrumReservation` carries no gateway, hub or site column and that `excl_reservation_overlap`
still keys on exactly `(spectrum_resource, allocated_rf, active_period)`.

## The three things this slice is really about

**Two answers, opposite directions, one register entry.** The most useful thing in this slice is
the pair of tests that pin both directions at once: two allocations at the same frequency, at
the same time, through the same Gateway are **both accepted** because their Beams' legs map to
different Spectrum Resources — and two allocations through *different* Gateways, at different
Hubs, on different Beams are **refused** because they share one payload input. Same file, four
lines apart. Either one alone would read as a permissive test or a strict one; together they are
**A-21**.

**A structural guard, not only a behavioural one.** The behavioural test above would keep
passing on the day somebody added a `gateway` column to the occupancy row and forgot to key on
it — and the day they *did* key on it, the failure would look like a spectrum bug in a slice
that had nothing to do with gateways. So the shape is asserted directly.

**"Display UTC explicitly" needed code.** Django renders a timestamp in the active zone and
prints no zone name, so `07:15` was telling a reader in Istanbul something different from what
it was telling one in Denver, and telling neither which. `{{ value|utc }}` converts and names
the zone.

The load-bearing half of that decision is what the filter deliberately is *not*: a per-request
`timezone.activate()`. That is the obvious way to offer a local display and it is the wrong one,
because the same activation that formats an output also **parses an input** — a `datetime-local`
field submitted under an active Istanbul zone would be stored three hours off, which is exactly
what *"shall not affect validation or stored values"* forbids. A test activates Istanbul and
asserts the rendering does not move.

## The golden-example harness — OQ-22

The answer asks the example to state the Payload Path Window, the Beam Spectrum Assignment, the
RF/IF conversion rule, the equipment limits, the validity periods, the requested allocation and
the expected free-capacity result — and three outcomes that are not arithmetic at all: rejection
through *another Hub, Beam or redundant ground site* on the same payload input, acceptance on an
independently implemented polarization, and rejection outside the assignment or its validity.

Those are the **OQ-25** cases. The golden example is meant to prove the reuse model end to end.

So there are now two harnesses over the same file:

| Harness | What it runs |
|---|---|
| `test_golden_examples.py` | The arithmetic — bandwidth, placement, translation, and now the RF/IF conversion against the stated LO and IF limits. Plus a **completeness check**: a file stating only the arithmetic no longer counts. |
| `test_golden_scenarios.py` | The platform. It builds the master data the file describes — two Gateways, three Beams, the resource mapping that makes them share or not share — allocates what the example asks for, compares the free capacity, and runs the four scenarios through the real services. |

`tests/domain/golden/` is still empty, and the gate still fails the build at Phase 9. **OQ-22
cannot be closed by building**, and this slice does not pretend otherwise: what it closes is the
gap between what the answer asks for and what a submitted file would be checked against.

**The scenario harness is proved to run.** A harness that has never executed is the worst kind —
it looks like coverage and would fail for its own reasons on the day a real example finally
arrives, by which point nobody can tell whether the file or the harness is wrong. A scaffolding
example in the test module exercises every path. Its numbers are arbitrary, hand-checkable and
labelled; nothing asserted from them is an RF claim, only that the *rules* hold.

## What the harness exposed

**The platform resolves entitlements and reservations as at *now*.** `spectrum.selectors` filters
active assignments and held reservations against the current instant, so an example whose
periods do not cover the present would produce an empty entitlement and refuse every scenario —
green-looking rejections that prove nothing.

The harness fails with that explanation rather than working around it, because working around it
would mean rewriting an engineer's dates. The answer asks for a *currently operational* Path
anyway, so a well-formed example will not hit it; the note is there for the one that does.

## Security and permission impact

- New view permissions on `Decimator` and `DecimatorAssignment`; no new capability.
- No change to scope. Neither model is scope-controlled: a Decimator is master data, like a
  Frequency Window, and the Path that references one is scoped by its Satnet as before.
- `SatnetPath.gateway` is a form field, and it is not a derived one — the operator states which
  Gateway the Path runs through. It reaches no engine and no constraint.

## Tests added

926 total, up from 892. 34 new.

| File | Covers |
|---|---|
| `tests/inventory/test_decimator_assignments.py` (11) | Overlapping configurations refused; **touching periods accepted** (**A-10**); a retired configuration overlapping a live one; two decimators over one period; an open-ended configuration blocking everything after it; inverted ranges refused by `int8range`/`tstzrange` before the CHECK is reached; stated parameters positive; **neither parameter required**; the table shipping empty |
| `tests/satnet_paths/test_hardware_references.py` (8) | Two Paths through one Gateway accepted; a shared resource still conflicting across Gateways; **no occupancy row carrying a gateway**; the exclusion key unchanged; GW ID a controlled reference with its label; many Paths sharing one Decimator Assignment; the Path pointing at the assignment rather than the box; both references optional |
| `tests/ui/test_utc_display.py` (11) | `TIME_ZONE`/`USE_TZ`; the filter naming the zone, converting rather than relabelling, and **ignoring an active local zone**; naive values read as UTC; the em dash; the format argument; a defaulted period starting now rather than at midnight; the wizard labelling its time fields; **a submitted `datetime-local` stored as the UTC instant**; an operational screen naming the zone |
| `tests/domain/test_golden_scenarios.py` (1 + parametrised) | The whole platform harness, exercised on scaffolding |
| `tests/domain/test_golden_examples.py` (+2 parametrised) | Completeness against **A-29**; the stated IF against the stated equipment |
| `tests/rf_confirmation/test_intake_templates.py` (+1) | The blank template asking for everything the answer requires |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.9 — guided creation with validation | **Held.** The wizard gains two controlled dropdowns in place of two free-text boxes. |
| §26.13 — blocking message content | **Held**, unchanged; the findings screen now names its periods in UTC. |
| §26.16 — derived values are engine-owned | **Held.** Neither new field is derived, and the form field-list test still passes. |
| §26.20 — no invented RF value | **Held.** Two new tables, both empty. No decimator, no configuration, no golden example. |

## Verification performed

```
pytest                                   926 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (11 modules, calculations strict)   no issues in 133 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          up to date
```

Three skips are new and all are the same thing: a parametrised harness over an empty directory.

## Remaining open questions

**OQ-22** — still the one gap that cannot be closed by building, and now with a sharper
definition of what closing it takes: a real operational example, independently calculated,
covering the reuse cases.

**OQ-36** (new) — must a Satnet Path's validity sit inside its Decimator Assignment's active
period? **A-25** already requires containment in three periods and a fourth would be consistent,
but the answer does not say so. The foreign key is recorded and not checked, and the gap is
stated in ADR-0021 rather than closed by assumption.

**OQ-31** — no tuning raster is enforced. Unchanged.

## What is deliberately not built

**No screen creates a Decimator.** The wizard offers whatever is in the table, which today is
nothing. Which decimators exist and how they are configured is site data, and the answer asked
for a model rather than a maintenance screen; the screen belongs with the S15 import work, where
the rest of the site data arrives.

**No containment check on the Decimator Assignment.** See **OQ-36**.

## Next slice

**S12 — Lifecycle, approvals and revisions**, as S11's report named it: the §15.2 transition
graph, second-person approval, and the `ON_AIR` revision that closes the old period before
opening the new one (**A-14**).
