# Slice S18 — Pilot, cutover and final acceptance

**Phase:** 9
**Report format:** Root Specification §27

---

## Goal, and the half of it that is blocked

S18's plan asks for four things:

> *Load validated operational data, run the controlled comparison against the spreadsheets,
> resolve differences, and produce the acceptance checklist with evidence.*

**The fourth is delivered. The first three cannot be done in this repository**, and the honest
report of that is most of what this slice is.

| | Status |
|---|---|
| Load validated operational data | **Blocked** — the nine §3.1 RF values do not exist yet |
| Run the controlled comparison against the spreadsheets | **Blocked** — **OQ-18**; there is no incumbent workbook |
| Resolve differences | **Blocked** — follows from the two above |
| Produce the acceptance checklist with evidence | **Delivered**, and machine-checked |

Simulating the first three would be worse than not doing them. A comparison against a
spreadsheet this project wrote compares the platform with itself; an inventory of plausible
frequencies is indistinguishable from real data the moment it is loaded, and every number
computed from it inherits the invention. §26.20 forbids exactly that, and the whole build has
held the line — this is not the slice to break it in.

## Files created or changed

**operations** — `acceptance.py`, `management/commands/acceptance_gate.py`

**Documentation** — `docs/acceptance-checklist.md`, this report, README

**Tests** — `tests/acceptance/test_acceptance_checklist.py` (12 tests, 87 cases — five are parametrised over all twenty criteria)

## Database impact

None. Nothing in this slice writes.

## The two things this slice is really about

**A checklist is a set of claims, so the claims are checked.** `docs/acceptance-checklist.md`
has one row per §26 criterion with a status, the test that evidences it and the commit that
delivered it — and `tests/acceptance/test_acceptance_checklist.py` verifies every reference in
it. Documents like this decay in a specific way: the criterion stays, the test it names gets
renamed, and the row goes on saying "Met" for years until somebody checks and finds the
guarantee was never there.

So the suite asserts that every cited file exists, that every cited commit is in the history,
that every status comes from a **fixed vocabulary** (so "essentially met" cannot appear), that a
row claiming `Met` cites at least one test, that evidence is a *test* rather than a source file
or a screenshot — because evidence has to be something that fails when the claim stops being
true — and that the summary's counts match its own table.

It found a real defect in its first run: the checklist listed the outstanding questions as
"OQ-01, 02, 03…", so a reader searching for `OQ-02` would have found nothing. Each is now named
in full, in a table, with where its answer lands.

**The gate is derived, never asserted.** `manage.py acceptance_gate` reads the §3.1 table **out
of the register itself**, counts the golden worked examples in the directory that would hold
them, and counts the rows in the tables each answer lands in. There is no hand-maintained "we
are ready" flag, because that is precisely the thing that goes stale the week before a cutover.

Today it reports, correctly:

```
Golden worked examples: none. OQ-22 cannot be closed by building …
9 RF engineering value(s) outstanding:
  OQ-01 … OQ-02 … OQ-03 … OQ-04 … OQ-06 … OQ-07 … OQ-14 … OQ-22 … OQ-24
CommandError: … This platform must not become the source of truth yet
```

**A non-zero exit is the correct answer today.** The command exists to stop "the application is
finished" being mistaken for "the application is the record" — two different sentences, and the
distinction is the entire subject of this slice.

An answer counts as settled only when it is **both** marked answered in the register **and**
present as rows. An answer that has been given and not loaded leaves the platform in exactly the
state it was in before, which is the state the gate exists to detect.

## The state of §26

| | |
|---|---|
| Fully met | **15** of 20 |
| Met in code, unproven against reality | **3** — §26.10, §26.11, §26.12, all **OQ-22** |
| Partial | **1** — §26.19, the legacy layout, **OQ-18** |
| Discipline met, gate open | **1** — §26.20, nine RF values outstanding |
| **Failing for want of implementation** | **0** |

That last row is the one worth reading twice. **Every gap is a missing input, not missing
code.**

### Why three criteria say "unproven against reality"

Not a euphemism, and deliberately not "Met". The engine computes bandwidth, edges, guards,
translation, RF/IF conversion and equipment matching, and every algebraic property holds under
property-based tests: translation round-trips, spectral inversion is its own inverse, guards
never overlap the occupied range, free plus allocated is the whole assignment.

None of that says the numbers are right for a real transponder. The fixtures use frequencies
this project invented to satisfy constraints. `tests/domain/golden/` holds a README and no
examples, and §24 is explicit that the example must come from a currently operational Satnet
Path calculated independently. **OQ-22 cannot be closed by building** — anything the
implementation produces to check itself against proves only that it agrees with itself.

`tests/domain/test_golden_examples.py` skips today and becomes a hard failure under
`VSAT_REQUIRE_GOLDEN_EXAMPLES=1`. A test in this slice fails the day a worked example appears,
with instructions — because the arrival of that file is the moment somebody has to act, and
nothing else in the repository would notice it.

## Security and permission impact

None. `acceptance_gate` reads the register, the filesystem and row counts; it writes nothing and
discloses no operational data — the counts are cardinalities, not rows.

It is deliberately **not** behind a capability. It is a build-and-deploy tool run from a shell
by whoever has the host, like `migrate` and `backup_database`, and reachable from no URL.

## Tests added

1444 total, up from 1355. 12 tests, 87 cases: five of them are parametrised over all twenty criteria, so a new criterion is checked the moment it is added.

| Area | Covers |
|---|---|
| The document (3) | It exists; every §26 criterion has exactly one row; every status is from the fixed vocabulary |
| The evidence (4) | Every cited file exists; every cited commit is in the history; a `Met` row cites a test; **evidence is a test rather than a source file** |
| The gate (5) | Every outstanding register item is named in the checklist; **§26.20 cannot read `Met` while the register is full**; the golden directory is empty and the checklist says so; the summary counts match the table |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.20 — no invented RF value | **Met as a discipline; the gate is open, and now measurable.** The register is read rather than restated, and a test refuses to let §26.20 be marked met while it is full. |
| §26.18 — documentation | **Advanced.** The checklist is the document Phase 9 sign-off is read from. |
| Every other criterion | **Reported**, with evidence, in `docs/acceptance-checklist.md`. |

## Verification performed

```
pytest                                   1444 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (14 modules, calculations strict)   no issues in 192 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          unchanged
acceptance_gate                          exit 1, nine outstanding values — the correct answer today
```

## Deviations from the plan

**Three of four deliverables are reported blocked rather than built**, with what each needs.
That is the deviation, and it is the point: the alternative was inventing RF values, which
§26.20 forbids and which every slice so far has refused.

**The checklist is machine-verified**, which the plan does not ask for. A checklist nobody
checks is a checklist that is wrong, and this one makes claims specific enough to check.

## What happens next, and in what order

Not a slice — a sequence for whoever picks this up:

1. **The nine §3.1 answers arrive** through `docs/rf-confirmation/`. Loaded as inventory rows;
   `acceptance_gate` starts reporting fewer outstanding.
2. **A worked example arrives** for `tests/domain/golden/`. `VSAT_REQUIRE_GOLDEN_EXAMPLES=1`
   goes into CI, §26.10/§26.11/§26.12 move to `Met`, and the three "unproven" rows close.
3. **The incumbent workbook arrives** (**OQ-18**). The legacy export and the legacy-layout
   import are sized together; §26.19 closes.
4. **The pilot runs.** Import a real plan as a dry run, compare the export against the
   spreadsheet, resolve differences with RF engineering. Everything needed for this exists:
   dry-run import (S15), export (S14), audit trail (S16), verified restore (S17).
5. **`acceptance_gate` returns zero.** That is the day the platform becomes the source of truth,
   and not before.

## Remaining open questions

**Nine §3.1 RF values** — OQ-01, OQ-02, OQ-03, OQ-04, OQ-06, OQ-07, OQ-14, OQ-22, OQ-24. Named
in full, with where each lands, in `docs/acceptance-checklist.md`.

**OQ-18** — the incumbent workbook, blocking both halves of the legacy layout.

**OQ-15, OQ-17, OQ-19** — volumes, network policy and the recovery objective. Each is stated
where it bites rather than pre-solved.

## Next slice

**None.** S18 is the last in the plan. What remains is the sequence above, and none of it is
code.
