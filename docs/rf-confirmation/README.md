# RF confirmation package

Slice **S0** of the roadmap. Specification §23 Phase 0, §24, and the `OPEN QUESTION`
register in `docs/design/00-assumptions-and-open-questions.md`.

This directory is how the platform asks for the values it refuses to invent.

## Why it exists

Acceptance criterion **§26.20** says that every unresolved RF rule must be a recorded open
question and that no value may be guessed. The platform holds to that: there is not one
frequency, one local oscillator, one guard width or one polarization mapping anywhere in the
codebase or its seed data. Every container is built and every container is empty.

That is the right position, and on its own it is not enough. A register of open questions
tells us what we do not know; it does not give anybody a way to tell us. This package is
that way.

## What is here

| File | What it is | Who fills it in |
|---|---|---|
| `templates/*.csv` | Six intake sheets, one per subject, with the exact columns the platform will load | RF engineering |
| `templates/golden-example.template.json` | One worked FWD or RTN example, for **OQ-22** | RF engineering |
| `column-guide.md` | What every column means, its unit, and what may go in it | — read, not filled |
| `oq-25-26-27-briefing.md` | The three questions that change the database schema | RF engineering — **this one is blocking** |
| `policy-decisions.md` | Five operational choices already built both ways | Operational policy owner |

## Where to start

**If you have ten minutes:** `oq-25-26-27-briefing.md`. Two of those three answers are what
the next slice is waiting on, and neither is a measurement — they are decisions about how
spectrum is shared, which nobody outside RF engineering can make.

**If you have an afternoon:** the sheets, in numbered order. They depend on each other in
that order: Windows reference Bands, Payload Paths reference Windows, polarization mappings
reference Payload Paths.

**Leave a cell empty when you do not know.** An empty cell is a recorded gap and the platform
will say so. A plausible value is indistinguishable from a confirmed one the moment it is
loaded, and that is the failure §26.20 exists to prevent — a number that looks like it came
from engineering, in a system people have started to trust.

`source_reference` and `engineering_reference` are not ceremony for the same reason. In
Phase 9 the platform's output is compared against the incumbent spreadsheets, and every
difference has to be explained. A figure whose provenance nobody can state cannot be
defended, and the argument will be about the platform.

## What is *not* asked for here

**Satellites, Gateways and Hubs.** Administrative records, entered through the application.
Nothing about them is an open RF question.

**Anything with a default.** A blank `min_edge_guard_hz` means zero, and zero is a real
answer. The column guide marks every column that is genuinely required.

**Beams, Satnets and allocations.** Operational configuration built in the application on
top of this data — not master data, and not yours to supply.

## Keeping the sheets honest

The sheets are **generated from the models** rather than maintained by hand:

```
python manage.py export_intake_templates
```

The requirement in `docs/design/05` is that each sheet carries *"the exact columns the
eventual import expects"*. A hand-written column list satisfies that on the day it is
written and stops being true at the next migration — and the mismatch surfaces after
somebody has entered four hundred rows under headings that no longer exist.

So `tests/rf_confirmation` checks it in both directions on every commit: the committed files
must equal what the generator produces, and every field the importer would need a value for
must be collected by some column or listed as deliberately not collected, with the reason.
Adding a required column to a model without extending its sheet fails the build.

## What happens to a completed sheet

Nothing automatic yet. The **S15** import slice builds the two-stage loader — a dry run that
reports every row and changes nothing, then a commit — and these columns are the contract it
is being built against. Until then a completed sheet is loaded by an administrator through
the application, and the sheet is the record of what was entered and who supplied it.
