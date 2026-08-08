# Slice S15 — Import: dry-run and commit

**Phase:** 7
**Report format:** Root Specification §27

---

## Goal

The two-stage import of §17.1: a dry run that reads a workbook, classifies every row into one of
seven outcomes and writes nothing operational, and a commit that verifies the file is still the
one that was reviewed and then writes the allocations — through the same service the wizard
uses, recalculating everything and trusting nothing the spreadsheet worked out.

## Files created or changed

**imports_exports** — `importer/{__init__,fields,parse,normalize,mapping,classify,commit}.py`,
`models.py` (three tables), `services.py` (`dry_run`, `commit_batch`), `selectors.py`,
`views.py`, `import_urls.py`, `constants.py`, `migrations/0002`; `export/safety.py` gains
`restore`, `export/workbook.py` names its two auxiliary sheets, `export/normalized.py` makes
`heading` public

**reporting** — two columns, `Input mode` and `Input value`

**satnet_paths** — `services.create` accepts an optional `path_id` and `revision_group`

**accounts** — two capabilities, `migrations/0015`

**config** — the `/imports/` mount

**Interface** — `templates/imports_exports/{list,review}.html`; the Imports entry in the
navigation becomes a link for an administrator

**Tests** — `tests/imports_exports/test_import.py` (70); one repaired test in
`tests/ui/test_utc_display.py`

**Documentation** — ADR-0015, this report

## Database impact

Three tables, exactly the ones `docs/design/02` §8 describes.

| Table | Holds |
|---|---|
| `import_batch` | The file's name, size and **SHA-256**, the stage, the batch policy, who read it and who committed it, the row count and the count per classification |
| `import_row` | One row per spreadsheet row: `raw`, `normalized`, `classification`, `messages`, `resulting_object_id`, and the row number **in the file** |
| `import_mapping` | `(kind, label) → target_id`, unique, so a label somebody once explained is never asked about again |

No exclusion constraint, no trigger. Nothing here holds spectrum — the allocations an import
produces are ordinary Satnet Paths, written through `satnet_paths.services.create`, and the
constraints that matter are already on them.

`import_batch.counts` is denormalised from `import_row`, which is a deliberate exception to the
rule ADR-0009 sets about computed values. The rows a count summarises are immutable once
written, so unlike free capacity this cannot drift — and the list screen shows seven numbers per
batch, which is a grouped query per row otherwise.

## The three things this slice is really about

**A value Excel calculated is never used.** The workbook is opened with `data_only=False`, so a
formula cell yields its *formula* and the cached number is never asked for. That text is refused
with the formula quoted back, never evaluated. Everything derived — bandwidths, guards, edges,
IF — is recomputed through the same service the wizard calls, against master data as it is now.

Where the file also carries a derived value it is **compared**: a disagreement makes the row a
`WARNING`, records both numbers and stores the platform's. That is the opposite of trusting the
file, and it is more useful than ignoring the column — a sheet that has been right for years and
is suddenly 250 kHz out is usually a guard policy nobody wrote down.

**A free-capacity row is never an allocation.** §17.1 says ignore them. ADR-0009 says why they
can never be anything else: free capacity is *computed* from what is allocated, so importing a
gap row would reserve the very spectrum it says is free, and the gap engine would then stop
reporting it — the platform would quietly lose capacity it had been told it had. The check runs
**first**, before anything is required of the row, because a gap row is usually missing most of
what an allocation needs and reporting it as `NEEDS_MAPPING` would send somebody to fix the
wrong thing.

**What commits is what was reviewed.** Two mechanisms answering two questions: the SHA-256
re-check proves the file in front of you is the file that was read, and writing from
`import_row.normalized` proves the numbers on the screen are the numbers that land. Either alone
leaves a gap — see ADR-0015 for the two ways it can be got wrong.

## What an import is allowed to produce

**Drafts, and only drafts.** An import is bulk data entry, not an approval (§12, §15.2). A
thousand imported allocations are a thousand decisions somebody still has to make, which is
heavier than a flag that activates them and is the only version that keeps §12's separation of
duties.

**Including the conflicting ones.** §17.1 asks for imported conflicts to be *reported and not
activated*. Discarding them is the easier reading and the wrong one: the allocation in the
incumbent spreadsheet is real, somebody is transmitting on it, and the overlap is the single
most valuable thing a migration surfaces. So the row is carried across as a draft, its findings
are recorded, and it holds no spectrum until a person decides. A draft reserves nothing
(**A-12**), which is what makes this safe rather than merely convenient.

## The seven classifications

| | Meaning | Committed? |
|---|---|---|
| `IGNORED_FREE_CAPACITY` | Spare spectrum, not a transmission | Never |
| `ERROR` | A cell could not be read, or a required one is empty | No |
| `NEEDS_MAPPING` | A label names nothing the platform holds | No |
| `DUPLICATE` | Already here, or twice in this file | No |
| `CONFLICT` | Recalculates, and the spectrum is taken | **Yes, as a draft** |
| `WARNING` | Recalculates, and the file disagrees about a derived value | Yes, as a draft |
| `VALID` | Nothing to say | Yes, as a draft |

Tested in that order. A row is often several at once, and the one it is reported as decides what
somebody does about it.

## The two batch policies

`docs/design/04` §8.4, and the policy chooses the transaction boundary and nothing else.
`ALL_OR_NOTHING` is one transaction and refuses before it starts if the batch holds a blocking
row; `ROW_BY_ROW` takes a savepoint per row and keeps what worked. An unrecognised value from
the form falls back to all-or-nothing, because the safe default is the one where a surprise
stops the batch.

## Reading a hostile file

Four things are refused before openpyxl is asked to read anything, and one per cell.

- **Macros.** A workbook containing `vbaProject.bin` is refused. openpyxl executes nothing — it
  is a ZIP and XML reader — but refusing it means nobody has to reason about what a later
  library version might do.
- **Decompression bombs.** The archive's central directory declares the expanded size; a file
  claiming more than 200 MB is refused *without being expanded*, which is the only point at
  which refusing it is cheap.
- **Size and shape.** 10 MB, and it must be a ZIP containing `xl/workbook.xml` — a `.csv` saved
  with the wrong extension gets a sentence rather than a stack trace.
- **External links.** `keep_links=False`, so nothing in the file can point at a path on the
  server.
- **Formulas**, per cell, as above.

**§21.12's guard is undone precisely.** An export writes a dangerous value as `'-Ka-1`; the
importer strips the apostrophe only where it precedes a formula character, so the code that went
out is the code that comes back and a real `=SUM(A1)` is still a formula. `restore` lives beside
`neutralise` in `export/safety.py` rather than with the importer that calls it: an inverse kept
somewhere else drifts the day a character is added to the guard list.

## Nothing is resolved by resemblance

A label matches a code exactly and case-insensitively, or it matches a mapping an administrator
recorded, or the row is `NEEDS_MAPPING`. There is no fuzzy matching anywhere, and there is not
going to be: a near-match resolved automatically would attach a transmission to the wrong
Satnet, which is the one mistake an unsupervised import must not be able to make. A Satnet code
that names two records (codes are unique per Beam, **A-18**) is reported as ambiguous rather
than resolved to whichever came first.

The review screen asks about each unknown label **once**, however many rows use it. A reviewer
asked the same question eighty times stops reading them.

## Security and permission impact

- Two capabilities, `run_import_dryrun` and `commit_import`, both **administrator only**
  (`docs/design/03` §2.1). Two rather than one because they are two decisions: reading what a
  file would do changes nothing, and writing what it says creates allocations across every Beam
  and Hub the file names. Object scope cannot narrow an import, because the file chooses what it
  touches — which is exactly why the capability is not given to an Operator.
- **Both stages are POST.** A dry run writes a batch; a commit writes allocations. Neither is
  something a link should be able to do to whoever clicks it.
- **Every import action is audited** (§18), including a refused one — a commit turned away
  because the file had changed is precisely the event somebody goes looking for. Recording a
  mapping is audited too: every future import of that label silently follows it.
- A refused commit leaves the batch **untouched**. Somebody uploading last month's file by
  mistake must not destroy a review that is still valid.
- `imports_exports/selectors.py` applies no scope filter, and says why in its docstring: import
  is administrator-only, an administrator holds every scope, and a check that can never fail
  reads like protection while providing none.

## Tests added

1194 total, up from 1124. 70 new, in `tests/imports_exports/test_import.py`.

| Area | Covers |
|---|---|
| Reading a hostile file (7) | A non-ZIP, an empty upload, an oversized one, a macro-enabled workbook, an absurd declared expansion, a file with no recognised column, the export's own auxiliary sheets skipped |
| Never trusting Excel (8) | **The workbook is opened `data_only=False`** — asserted structurally; a formula refused and quoted rather than evaluated; a disagreeing derived column becoming a `WARNING` **and the engine's value being what commits**; a unit suffix refused rather than converted; a fractional hertz refused; thousands separators forgiven; a naive timestamp read as UTC; §21.12's guard surviving a round trip while a real formula is still refused |
| The seven classifications (12) | One test each, plus **all seven from one file**, five free-capacity markers, and an unnamed row carrying a frequency |
| Free capacity (1) | **The property**: after a commit, a `FREE` row has produced no Satnet Path |
| The dry run (4) | No allocation and no reservation written; the file, size and digest recorded; audited; the row number is the file's own |
| The SHA-256 seam (5) | A changed file refused and the batch left in `DRY_RUN`; the refusal audited; the same file committing; a second commit refused; the row pointing at what it produced |
| What an import produces (4) | Everything a `DRAFT`; **a conflict imported, reported and holding no spectrum**; a stable identifier honoured; an unreadable one an error rather than ignored |
| Batch policies (5) | All-or-nothing refusing and writing nothing; row-by-row keeping what worked; a write that fails after classification recorded as a row rather than raised, and stopping an all-or-nothing batch with a sentence; an unrecognised policy falling back |
| Re-checking on commit (1) | A row whose spectrum was taken after the review is reclassified and says so |
| Mappings (5) | A remembered label resolving next run; audited; a near-match never resolving; case-insensitive codes; one question per label |
| The round trip (2) | The export's headings are what the importer expects; **an export read back creates nothing** |
| Authorization (5) | Each non-admin role refused at the service, at both stages; the denial audited; anonymous and Operator refused at the screen |
| The screens (6) | Reading a file, the review page showing all seven counts including zeroes, a commit needing the file again, a commit writing, an unreadable upload reporting its reason, the navigation entry |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.17 — traceability | **Advanced.** Every import action is an audit event carrying its batch id, and `import_row` is a permanent record of what each spreadsheet row said and what became of it. |
| §26.19 — import and export | **Met for the import.** Two stages, seven classifications, SHA-256 verified, free-capacity rows ignored. The legacy *layout* remains **OQ-18**. |
| §26.16 — derived values are system-owned | **Held, and tested from a new angle.** A file asserting a different bandwidth gets a warning and the engine's number. |
| §26.14 — no bypass of the overlap constraint | **Held.** An import cannot activate anything; a conflicting row lands as a draft holding nothing. |
| §26.20 — no invented RF value | **Held.** A frequency carrying its own unit is refused rather than converted. |

## Verification performed

```
pytest                                   1194 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (14 modules, calculations strict)   no issues in 173 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          unchanged
```

## Deviations from the plan

**`importer/`, not `import/`.** The plan's directory name is a Python keyword —
`from imports_exports.import import parse` is a syntax error.

**Six modules, not five.** `fields.py` holds the column contract, which `parse`, `normalize`,
`classify` and the views all consult. Putting it inside any one of them would make the other
three import that one for a reason unrelated to what it does.

**`mapping.py`, not `map.py`.** It is about `ImportMapping`, and the plan's name reads as a verb.

**No `templates/imports_exports/` wizard.** Two pages: a list with an upload form, and a review
screen. The plan says "review UI templates" and that is what these are.

**Two columns added to the Satnet Path table.** §9.2's `input_mode` / `input_value` pair is the
only thing in the record recalculation cannot recover, so an export without it cannot be
imported back into an equivalent allocation. They are not shown by default.

**`satnet_paths.services.create` gained two optional arguments.** `path_id` and `revision_group`
exist for the importer and nothing else — "stable UUIDs honoured" is not expressible without
them. The wizard passes neither.

**One repaired test outside this slice.** `tests/ui/test_utc_display.py` hard-coded
`2026-08-05T12:00`, which fell behind its fixture's validity window as the calendar moved; it
failed on `main` before this slice began. The moment is now derived from the clock, and the
docstring says why.

## What this import deliberately does not do

**It does not read the incumbent workbook.** Its vocabulary is the platform's own export —
headings from `reporting.columns`, which come from the Specification Dictionary. Reading the
legacy layout is **OQ-18** and needs the real file; the S14 report sets out why guessing at a
layout is worse than refusing to.

**It does not set a guard policy, a decimator or an equipment profile.** Each would make the
importer choose engineering. A guard policy is an override that would silently change the guards
on every row carrying it; a decimator assignment is time-bounded, so a box name does not
identify one (ADR-0021). Where the file's guard widths disagree with the resolved ones, the row
is warned about rather than obeyed.

**It does not update an existing allocation.** A row matching something already here is a
`DUPLICATE`. Changing an allocation is an edit or a revision (§15.4), which is a decision
somebody makes on the record — not something a spreadsheet does to a hundred records at once.

## Remaining open questions

**OQ-18** — the incumbent workbook. Both halves of §17 now wait on the same sample: the legacy
export cannot be written and the legacy *layout* cannot be read. The normalized shape works end
to end in both directions today.

**OQ-15** — expected volumes. A dry run holds the parsed sheet in memory and writes one
`import_row` per spreadsheet row. Right at the tens of thousands the guard rails allow; a file
an order of magnitude larger wants streaming and a chunked `bulk_create`. Stated rather than
pre-solved.

**OQ-22** — still the one gap that cannot be closed by building.

## Next slice

**S16 — Audit UI.** The trail S2 started writing and every slice since has written to, given a
screen: search by actor, object, action and period, with the import batch id as a first-class
filter now that there is something to filter by.
