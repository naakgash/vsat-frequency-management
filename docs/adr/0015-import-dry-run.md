# ADR-0015 — An import is read twice and calculated once

**Status:** Accepted
**Date:** 2026-08-08
**Slice:** S15 — Import: dry-run and commit
**Specification:** §17.1, §9.5, §26.16, §26.17
**Assumptions:** **A-08**, **A-23**, **A-28**
**Open questions:** **OQ-18**

## Context

§17.1 asks for a two-stage import: a dry run that says what a file would do, and a commit that
does it. It also says the importer must never trust a value Excel calculated, must ignore
free-capacity rows rather than importing them as allocations, and must classify every row into
one of seven outcomes.

The temptation in every one of those requirements is to treat the spreadsheet as an authority.
It is not. It is a record of what somebody believed, maintained by hand, containing arithmetic
that was right when it was typed and formulas whose inputs have moved since. The whole reason
this platform exists is that a spreadsheet cannot be relied on to hold the frequency plan.

Three specific problems.

**A spreadsheet carries two answers to every calculated cell.** The formula, and the number
Excel last cached for it. The cached number is what most importers read, because it is a number
and reading it is one line. It is also a value computed by another program, at an unknown time,
possibly against inputs that were subsequently edited — and once it is written to the database
it is indistinguishable from a value the platform computed itself.

**A file can change between the review and the click.** The reviewer reads a screen produced
from a file on Monday. On Wednesday they press Commit. Nothing in a batch identifier says the
file is the same one.

**A free-capacity row looks exactly like an allocation.** Same columns, same frequencies, and
the only difference is a word in an identity cell. Imported as an allocation it would reserve
the spectrum it exists to say is free, and the gap engine (ADR-0009) would then stop reporting
it — the platform would quietly lose capacity it had been told it had.

## Decision

**Read the formula, never the cached value.** The workbook is opened with `data_only=False`, so
a formula cell yields its formula text. That text is refused with the formula quoted back, and
the cell is never evaluated. There is no configuration that turns this off.

**Recalculate everything derived, and compare rather than trust.** The importer reads only the
operator's inputs — code, Satnet, direction, §9.2's input mode and value, roll-off, centre
frequency, validity — and hands them to `satnet_paths.services.create`, the same service the
wizard uses. Every bandwidth, guard, edge and IF value is computed here and now against current
master data (§26.16).

Where the file *also* carries a derived value, it is read and **compared**. A disagreement makes
the row a `WARNING`, records both numbers, and stores the platform's. That is not trusting the
file; it is the opposite, and it is more useful than ignoring the column, because a spreadsheet
that has been right for years and is suddenly 250 kHz out is usually a guard policy nobody wrote
down.

**A commit verifies the file's SHA-256 and then writes from the reviewed rows.** Both halves,
because they answer different questions:

| | What it proves |
|---|---|
| SHA-256 re-check | The file in front of you is the file that was read |
| Writing from `import_row.normalized` | What lands is what the screen displayed |

Verifying the hash and then re-parsing would satisfy §17.1 literally and still allow the reading
to differ, because a second parse is a second interpretation. Writing from the stored rows alone
would let somebody commit a batch while holding a file they had since edited, and believe the
two agreed.

The cost is that the file has to be attached again at commit. That is a real cost and it is the
right one: the alternative — keeping the uploaded bytes on the server — proves only that the
server did not corrupt them.

**Rows are judged again on commit.** The same reason §9.5 makes the wizard re-check on save: a
batch reviewed on Monday was classified against reservations that have since changed. A row
whose classification moved is updated and says so, so `import_row` ends up describing what
happened rather than what was expected.

**Every imported allocation is a `DRAFT`.** An import is bulk data entry, not an approval. A
draft holds no spectrum (**A-12**), which is what makes the next decision possible:

**A conflicting row is imported, and holds nothing.** §17.1 asks for imported conflicts to be
*reported and not activated*. Discarding them would be the easier reading and the wrong one:
the allocation in the incumbent spreadsheet is real, somebody is transmitting on it, and the
overlap is the single most valuable thing a migration can surface. So the row is carried across
as a draft, its findings are recorded on the `import_row`, and it reserves no spectrum until a
person decides what to do about it.

**A free-capacity row is classified first, before anything is required of it.** Ahead of the
missing-cell check and ahead of reference resolution, because a gap row is usually missing most
of what an allocation needs — and reported as `NEEDS_MAPPING` it would send somebody to create a
mapping for a row that should never have been read.

**An identifier in the file is honoured.** A Satnet Path exported and read back is *the same
allocation*, not a copy: `id` and `revision_group` are passed through to the create service. A
re-import that minted new identifiers would double the plan every time somebody ran it.

**Nothing is resolved by resemblance.** A label matches a code exactly, case-insensitively, or
it matches a mapping an administrator explicitly recorded, or the row is `NEEDS_MAPPING`. A
near-match resolved automatically would attach a transmission to the wrong Satnet, which is the
one mistake an unsupervised import must not be able to make.

## The seven classifications, in the order they are tested

| | Meaning | Committed? |
|---|---|---|
| `IGNORED_FREE_CAPACITY` | Spare spectrum, not a transmission (§17.1, ADR-0009) | Never |
| `ERROR` | A cell could not be read, or a required one is empty | No |
| `NEEDS_MAPPING` | A label names nothing the platform holds | No |
| `DUPLICATE` | Already here, or twice in this file | No |
| `CONFLICT` | Recalculates, and the spectrum is taken | **Yes, as a draft** |
| `WARNING` | Recalculates, and the file disagrees about a derived value | Yes, as a draft |
| `VALID` | Nothing to say | Yes, as a draft |

Order matters because a row is often several of these at once, and the one it is reported as
decides what somebody does about it.

## Consequences

**An import cannot put anything on air.** Approval stays a human decision on a record (§12,
§15.2). A migration of a thousand allocations produces a thousand drafts and a thousand
decisions — which is heavier than a flag that activates them, and is the only version that
keeps the separation of duties §12 exists for.

**The importer reads the platform's own export, not the incumbent workbook.** The vocabulary is
the export's headings, which come from `reporting.columns`, which come from the Specification
Dictionary — one chain, so a renamed column breaks in one visible place. Reading the incumbent
layout is **OQ-18** and needs the real file; see the S14 report for why guessing at it is worse
than refusing.

**Two columns were added to the Satnet Path table** — `Input mode` and `Input value`. §9.2's
pair is the only thing in the record that recalculation cannot recover, so an export without
them cannot be imported back into an equivalent allocation.

**A guard policy, a decimator and an equipment profile are not imported.** Each would make the
importer choose engineering: a guard policy is an override that would silently change the
guards on every row carrying it, and a decimator assignment is time-bounded, so a box name does
not identify one (ADR-0021). Where the file's guard widths disagree with the resolved ones, the
row is warned about rather than obeyed.

**`import_row` is a permanent record.** Every row a file contained, what it was read as, what it
was judged to be and why. Not a cache: it is what the commit writes from, and it is what answers
"why did this allocation appear" a year later.

## Alternatives rejected

**Read the cached values (`data_only=True`).** One line, and it produces rows whose arithmetic
came from an unknown program at an unknown time. Indistinguishable from correct data once
written, which is what makes it the worst option rather than merely a lax one.

**Commit from a second parse of the file.** Satisfies §17.1's hash check and still allows the
committed interpretation to differ from the reviewed one — a difference nobody would look for,
because the hash matched.

**Store the uploaded file and hash that at commit.** Proves the server did not corrupt its own
copy, and nothing about whether the reviewer is committing what they reviewed.

**Discard conflicting rows.** Loses exactly the rows a migration exists to find. Reported and
holding nothing is strictly more informative and no more dangerous.

**Import as `PLANNED` so allocations hold their spectrum immediately.** Faster cutover, and it
lets an import approve work by omission. It also means the first import of a plan containing a
genuine overlap fails halfway through on the exclusion constraint, with no record of which rows
were the problem.

**Fuzzy label matching.** Would resolve most labels correctly and some incorrectly, and the
incorrect ones attach a transmission to the wrong Satnet without saying so.
