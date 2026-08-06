# Slice S14 — Export

**Phase:** 7
**Report format:** Root Specification §27

---

## Goal

The normalized Satnet Path export: an `.xlsx` file carrying what the table shows, with the
identifiers an import will match on, a Data Dictionary sheet, a record of what produced it — and
formula-injection protection on every cell (§21.12).

The legacy-style export is **not** built. That is **OQ-18** and the reason is below.

## Files created or changed

**imports_exports** (new module) — `export/{safety,workbook,normalized,legacy}.py`,
`services.py`, `views.py`, `urls.py`, `constants.py`, `models.py`, `apps.py`,
`migrations/0001`

**accounts** — one capability, `migrations/0014`

**config** — `imports_exports` in `INSTALLED_APPS` and the URL mount; `openpyxl` added to the
project dependencies and the lock file

**Interface** — an Export button on the table, carrying the current query string

**Tests** — `tests/imports_exports/test_export.py` (30)

**Documentation** — this report

## Database impact

None. `ExportPolicy` is `managed = False`: Django permissions belong to a model, and exporting
is an action rather than a record — the only thing stored about a completed export is its audit
event (§18). The migration creates the permission and no table.

The import batch tables `docs/design/02` §8 describes arrive with S15, which is when there will
be something to put in them.

## The three things this slice is really about

**A cell is not a formula, and the platform is what decides that.** A Satnet Path code is text
an operator typed. Excel reads a cell beginning `=`, `+`, `-` or `@` as a formula, and formulas
reach the shell through DDE — so a code like `` =cmd|'/c calc'!A1 `` is a working attack on
whoever opens the export. Every value is neutralised on the way *in*, at a single choke point:
`workbook.write_row` is the only function that puts a value in a cell, so §21.12 is not
something each sheet has to remember.

The **value** is neutralised, not the display. openpyxl can set `quotePrefix`, which tells Excel
to treat the content as text while leaving the stored string starting with `=` — prettier, and
not enough: the moment the workbook is saved as CSV or read by anything that ignores the flag,
the formula is back. An apostrophe in front of the value survives all of that, at the cost of
one visible character on the cells that were dangerous.

**The export is the table.** Same columns, same filters, same scope-filtered queryset, because
an export that answered a slightly different question from the screen somebody was looking at is
worse than no export: they reconcile the difference by hand and conclude the platform is wrong.
`reporting.selectors.table` is called directly, which is also why `imports_exports` sits *above*
`reporting` in the module layering.

**A file has to say where it came from.** Three sheets, always: the data, a **Data Dictionary**
read from the Specification Dictionary rather than restated (§2, so an administrator's edited
description reaches the next export), and an **Export** sheet naming who ran it, when in UTC,
with which filters and columns, and how many rows came back. §17.2 asks for the filter
parameters, and the reason is that an export without them is a number nobody can reproduce.

## What xlsx cannot carry, and what was done about it

**The format has no time zones.** openpyxl refuses an aware datetime outright, which puts a
genuine collision in front of **A-28**: every timestamp in this platform states its zone.

Two ways out — write an ISO string with the offset, or convert to UTC and write a real datetime
cell. This takes the second, because a text timestamp cannot be sorted or filtered as a date and
that is most of what somebody opens a spreadsheet to do. The zone moves to the **column
heading**: a timestamp column is headed `Valid from (UTC)`. A-28's rule is kept — a timestamp
always says which zone it is in — and the cell stays a date.

Frequencies go out as **integer Hz**, not as rendered MHz (**A-08**): a spreadsheet that
received `29,145.000` would have to parse a thousands separator to get a number back. A Decimal
roll-off is written as a Decimal, because openpyxl stores it exactly and converting to float
here would undo ADR-0003 at the last possible moment.

## OQ-18 — why the legacy export is a raise and not a stub

§17.2 asks for an export matching the incumbent spreadsheet closely enough that today's users
can keep working. That is a real requirement and it is not implementable from the specification:
it needs the actual workbook — sheet names, column order, merged headers, the unit each column
is written in, and whatever conventions have accumulated in it.

Written from a description it would look approximately right and be wrong in ways nobody notices
until the Phase 9 migration comparison shows differences that turn out to be the export's fault
rather than the engine's. So `legacy.build()` raises with the list of what is missing.

A stub returning an empty workbook would be worse than this: somebody would ship it, and an
empty file is indistinguishable from a filter that matched nothing.

## Security and permission impact

- One new capability, `imports_exports.export_data`, held by **every** role. §17.2 narrows an
  export by *scope*, not by capability: an Observer exporting "all Satnet Paths" receives the
  same queryset the screen would have shown them, which is exactly what reusing the table's
  selector guarantees.
- **Every export is audited.** The event records the filters, the columns, the row count and the
  filename — not the rows. The trail is a record of what happened, not a second copy of the
  data it happened to.
- The download is a **GET**. An export changes nothing, and making it a POST would break the one
  thing people do with these links: paste them to each other.

## Tests added

1116 total, up from 1082. 30 new.

| File | Covers |
|---|---|
| `test_export.py` (30) | Each formula-start character neutralised and each ordinary value left alone; a negative number staying a number; **no cell anywhere in a produced workbook being dangerous**; the neutralised value still readable; row counts; **the export matching the table**; identifiers present and round-tripping as UUIDs; frequencies in Hz; chosen columns and specification-code headings; the Data Dictionary matching the dictionary, including after an edit; the Export sheet's provenance and the "no filters" case; exactly three sheets; scope never widened; every role exporting; an anonymous caller refused; the audit event; four HTTP paths including the table's own Export link; **the legacy export refusing with its reason** |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.19 — export with a data dictionary | **Met** for the normalized export; the legacy layout is **OQ-18**. |
| §26.17 — traceability | **Advanced.** Every export is an audit event, and the file itself records what produced it. |
| §26.11 — table and capacity | **Held.** The export reuses the table's selector rather than reimplementing it. |
| §26.20 — no invented RF value | **Held.** |

## Verification performed

```
pytest                                   1116 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (14 modules, calculations strict)   no issues in 164 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## Deviations from the plan

**No `templates/imports_exports/`.** The plan lists one; the export is a download with no screen
of its own, reached from a button on the table that carries the current query string. A page
whose only content is "click here to download what you were already looking at" would be a step
in the way.

**One new runtime dependency**, `openpyxl`, which the planned stack already named. It is in
`pyproject.toml` and `uv.lock`; the container installs from the lock file, so nothing else
changes.

## Remaining open questions

**OQ-18** — the incumbent workbook. Until a real sample arrives, the legacy export raises rather
than guesses.

**OQ-15** — expected volumes. The export builds the whole workbook in memory, which is right at
10⁵ rows and would need streaming well above that. Stated rather than pre-solved.

**OQ-22** — still the one gap that cannot be closed by building.

## Next slice

**S15 — Import: dry-run and commit.** The two-stage import that recalculates everything through
the same services and never trusts an Excel-calculated value, with the seven row
classifications, the SHA-256 verified between dry run and commit, and free-capacity rows ignored
rather than imported as allocations (§17.1).
