# Slice S0 — RF Domain Confirmation Package

**Phase:** 0
**Report format:** Root Specification §27
**Built out of order:** S0 is Phase 0 and non-blocking for S1–S8, so it was deferred until it
was the thing standing in the way. It now is.

---

## Goal

Turn the `OPEN QUESTION` register into something RF engineering can answer, and get
**OQ-25** and **OQ-27** answered before S9.

Every slice since the design pass has ended with the same sentence: the containers ship, the
values do not. That position is correct — §26.20 requires it — and on its own it is only half
a system. A register tells us what we do not know. It gives nobody a way to tell us.

## Files created or changed

**inventory** — `intake.py` (the sheet declarations and the field-reading machinery),
`management/commands/export_intake_templates.py`

**Package** — `docs/rf-confirmation/`: `README.md`, `oq-25-26-27-briefing.md`,
`policy-decisions.md`, the generated `column-guide.md`, six generated
`templates/*.csv`, and `templates/golden-example.template.json`

**Tests** — `tests/rf_confirmation/test_intake_templates.py` (19)

**Fixed** — `tests/domain/test_golden_examples.py`, a real defect found by this slice
(below)

**Tooling** — `make intake`; one file added to the terminology allow-list

## Database impact

**None.** No model changed, no migration, no seed data. The package reads the models; it does
not touch them.

## Why the sheets are generated rather than written

`docs/design/05` requires each workbook to carry *"the exact columns the eventual import
expects"*. That is a promise about a slice that does not exist yet — S15 — and a hand-typed
column list keeps it on the day it is written and quietly stops keeping it at the next
migration.

The cost of that particular drift is unusual. Most stale documentation is discovered by the
next person to read it. This kind is discovered by an RF engineer who has already entered
four hundred rows under headings the importer will reject, and the work is not recoverable
by fixing the code.

So a sheet **declares columns against a model**, and each column's unit, whether it is
required, and its permitted values are **read from the field** rather than restated. Then the
tests run in both directions:

- **Forward** — the committed files equal what the generator produces. Change a model, forget
  the package, and CI says so.
- **Backward** — every field an importer would need a value for is either collected by a
  column or listed in `supplied_by_the_platform` **with the reason**. Add a required column to
  `FrequencyWindow` and the build fails until somebody decides whether it is RF engineering's
  to answer or the platform's to write.

The backward direction is the one worth having. It was verified by deleting the `lo_hz`
column and confirming the failure names the field, the model and both remedies.

## The three-line judgement that shaped the package

**One header row per CSV, not four.** Units and permitted values would be more convenient
inline, and they would also be three rows the S15 importer has to learn to skip — in a file
whose whole purpose is to be the thing the importer reads. The guidance went to
`column-guide.md` instead, where a person reads it and no parser has to.

**A seventh sheet.** The plan names six subjects; Bands make seven. **OQ-14** (which
polarizations are in use) and **OQ-31** (whether modems tune on a raster) are RF facts with
no other container. A Band's identity is administrative, those two attributes are not, and
without a sheet they would be settled at data-entry time by whoever was typing.

**No sheet for Satellites, Gateways or Hubs.** They are administrative records with no
unanswered RF question attached. Asking for them would pad the package and imply that
somebody's answer was being waited on.

## The briefing is the deliverable

`oq-25-26-27-briefing.md` is what S9 is actually waiting on, and it does one thing the
register does not: for each question it states **what the answer costs now against what it
costs after S9**, in the concrete terms of the constraint key.

It also offers a reading rather than only a question. For **OQ-25** the four shapes an answer
can take are laid out with their schema consequence, and the third — *shared per Gateway on
the hub uplink leg, free per Beam on the remote leg* — is named as our reading, with the
physical reasoning stated so it can be disagreed with. A question with no proposed answer
invites a meeting; a proposed answer invites a correction, which is faster and more useful.

**One correction to what every slice report since the design pass has said.** OQ-26 was
listed alongside OQ-25 and OQ-27 as blocking S9. It is not. Remote-terminal equipment adds a
second profile reference and a second IF range to the **Satnet Path** table, which S11
builds — it does not appear in the overlap constraint at all. S9 needs **OQ-25 and OQ-27**.
Saying otherwise made the gate look wider than it is and left one more answer to wait for
than the work requires.

## Security and permission impact

**None.** No route, no capability, no view. The command is a developer tool run from a shell
by someone who already has the repository, and it writes only inside `docs/`.

Worth stating explicitly: the package contains **no credentials, no hostnames and no
operational data**. It is a set of empty column headings and three documents about questions.
It is written to be sent outside the team, which is the only way it does its job.

## Tests added

666 total, up from 621. 45 new — ten test functions, most parametrised over the six sheets —
plus one fix.

| Covers | |
|---|---|
| Drift, forward | Every committed sheet equals the generator's output; the column guide too |
| Drift, backward | Every required model field is collected or explained; no explanation survives the field ceasing to be required |
| Integrity | Every column names a real field; every lookup points at a relation that has a `code` |
| §26.20 | Every sheet has exactly one row — the headings — and the golden template's every value is empty |
| Provenance | Every sheet cites an open question that exists in the register |
| The golden template | Filled with placeholder numbers and run through the harness's own loader, so a renamed key fails here rather than silently ignoring an example somebody filled in correctly |

### A defect the template found

The golden template carries `"percent_left": null` — the right thing for a `FIXED`-mode
guard, where the percentage columns do not apply. Running it through the harness raised
`TypeError: conversion from NoneType to Decimal is not supported`.

`test_golden_examples._guard_policy` tested for the **key**, not the value:

```python
percent_left=Decimal(guard["percent_left"]) if "percent_left" in guard else None
```

A well-formed example written from the template would have crashed the harness. Nobody would
have found it until the first real golden example arrived — the one moment when a confusing
failure is most expensive, because the natural reading is that the *engine* is wrong.

It now tests the value. This is the second time in this build that writing the thing that
produces the input has found a bug in the thing that consumes it, and both times the bug was
in code that had passed review and had tests.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.20 — no invented RF value, every unresolved rule recorded | **Advanced.** The register recorded the gaps; this slice supplies the means to close them. Nothing in the package contains a value. |
| §26.18 — documentation and ADR discipline | **Held.** No ADR: the slice makes no architectural decision. The one judgement worth recording — generate rather than hand-write — is in the module docstring and here. |

## Remaining open questions

All of them. This slice closes none — it is the instrument, not the answer.

**Blocking S9:** OQ-25, OQ-27.
**Briefed alongside them but not blocking S9:** OQ-26.
**Now has a sheet to arrive on:** OQ-01, OQ-02, OQ-03, OQ-04, OQ-07, OQ-14, OQ-22, OQ-31,
OQ-34.
**Now has a decision sheet:** OQ-05, OQ-08, OQ-11, OQ-13, OQ-23.

Still with no container and needing none until later: OQ-06, OQ-09, OQ-10, OQ-12, OQ-15 to
OQ-21, OQ-24, OQ-28, OQ-29, OQ-30, OQ-32, OQ-33, OQ-35.

## Next slice

**S9 — Reservations, exclusion constraints and the gap engine**, once OQ-25 and OQ-27 are
answered. It is the slice where the platform stops describing spectrum and starts
guaranteeing that two allocations cannot occupy the same Hz, and it is the wrong slice to
build twice.

Until those answers arrive there is no unblocked slice that is honest to start. S10 needs the
reservation table for its capacity summary; S11 needs S10; S12 needs S11. The remaining work
is downstream of the guarantee S9 provides, which is the correct shape for a platform whose
central promise is that guarantee — and it is why the briefing, rather than more code, is
what this slice delivers.
