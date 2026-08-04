# Slice S9 — Reservations, the exclusion constraint and the gap engine

**Phase:** 4
**Report format:** Root Specification §27

---

## Goal

The platform stops describing spectrum and starts **guaranteeing** it: two allocations cannot
occupy the same Hz on the same resource at the same time, and the guarantee holds against
concurrency, the importer, a migration and a `psql` session.

Built **before** the Satnet Path wizard that will write reservations, which is the whole point
of the ordering in `docs/design/05`: *"the concurrency test exists before the first reservation
is ever written, instead of being added afterwards to a system already assumed correct."*

## Files created or changed

**spectrum** (new) — `models.py`, `constants.py`, `services.py`, `selectors.py`, `views.py`,
`urls.py`, `apps.py`, `migrations/0001_initial.py`, `migrations/0002_assignment_composite_key.py`

**calculations** — `gaps.py`, pure and added to the purity contract

**Interface** — `templates/spectrum/beam_spectrum.html`, the occupancy strip in `app.css`, a
link from the Beam detail screen, the navigation entry

**Tests** — `tests/spectrum/{factories,test_exclusion,test_concurrency,test_capacity,test_reserve}.py`,
`tests/domain/test_gaps.py`

**Documentation** — ADR-0007, ADR-0009, ADR-0017, this report

**Tooling** — `spectrum` added to the import-linter root packages, the layer list and the
`types` gate

## Database impact

| Table | Notes |
|---|---|
| `spectrum_reservation` | One occupancy row per resource per leg. Generated `int8range` and `tstzrange` columns, seven CHECKs, the exclusion constraint, and a partial GiST index |

**The constraint, and what is no longer in it:**

```sql
EXCLUDE USING gist (
    spectrum_resource_id WITH =,
    allocated_rf         WITH &&,
    active_period        WITH &&
)
WHERE (reserves_spectrum)
```

Three columns where the pre-OQ-25 design had six. Beam, Frequency Window, leg and polarization
are all now properties *of* a resource or irrelevant to whether two allocations compete
(ADR-0018).

`allocated_rf`, not `occupied_rf`: §8.1 makes the reserved interval the allocated one, and a
constraint on the occupied range would accept two transmissions whose guards overlap —
`test_the_constraint_compares_allocated_not_occupied` is the case.

**One allocation writes N ≥ 2 rows**, not two (**A-23**). ADR-0006's two-sided model is still
true of the engineering; it is no longer the row count.

**`satnet_path_id` has no foreign key yet.** `satnet_path` does not exist until S11, and that
is the ordering working rather than a gap: S11 adds the key to a table that is still empty.

## The defect the concurrency test found

`tests/spectrum/test_concurrency.py` raced two connections at a barrier. It failed
intermittently — roughly one run in three — and the reason was not a broken guarantee.

The losing writer does not always get an `IntegrityError`. When both `INSERT`s are in flight at
once, each takes a lock the other needs *while checking the exclusion constraint*, and
PostgreSQL breaks the tie with `OperationalError: deadlock detected`. Exactly one row still
survives, every time. But a service catching only `IntegrityError` would hand whoever lost the
race an unhandled exception — a 500 — on an ordinary Tuesday-morning collision between two
people planning at the same moment.

Two changes came out of it:

- **`spectrum.services.SpectrumConflictError`** translates both shapes into one thing a caller
  can act on, carrying `was_deadlock` because it changes what a retry can achieve. The
  translation is narrow: an unrelated `IntegrityError` still propagates, because reporting a
  violated CHECK as "this spectrum is taken" sends somebody looking for a competing allocation
  that does not exist. `test_an_unrelated_integrity_error_is_not_disguised_as_a_conflict`.
- **The test asserts the invariant, not the failure shape.** Which writer loses is arbitrary
  and *how* it loses depends on timing, so asserting either would make the test flaky — the
  worst possible property for the test guarding the product's central promise. It now asserts
  one row, and ran twenty consecutive times without a failure.

This is the second time in this build that a test written against real concurrency found
something no amount of reading the code would have.

## Free capacity is calculated, never stored

ADR-0009. §16 requires the gaps, the largest, the utilisation; a `free_capacity` table would
make the dashboard one query and would be a second source of truth for the one fact the product
exists to be right about. When it disagreed with the reservations, nothing would say which was
right — and the failure points the wrong way: a stale cache reports spectrum as free, an
operator allocates it, and the constraint refuses a placement the platform had just offered.

Two things the selector deliberately does:

- **Bounds are the Beam's active assignments, not its Frequency Window** (ADR-0019), because
  the OQ-27 answer says so and because a window may be shared. Computing across it would offer
  an operator spectrum belonging to another Beam.
- **Occupancy comes from the resource, not the assignment.** Everything holding spectrum on the
  resources a leg occupies counts against it, *including other Beams' allocations*. That is the
  OQ-25 answer showing up on a screen, and
  `test_another_beams_reservation_on_a_shared_resource_counts_against_this_one` is the case the
  superseded A-01 would have got wrong.

## A deliberate deviation from the plan

`docs/design/05` lists an "ECharts spectrum map (vendored)". This ships a **CSS occupancy
strip** instead.

§19.4 forbids a CDN, so every charting library is a binary this repository owns forever — and a
spectrum strip is a set of rectangles on one linear axis, which is what a positioned `<span>`
already is. Every figure it shows is also present as text in the tables beside it, so nothing
is locked inside a canvas a screen reader cannot reach. S13's dashboard may genuinely want
charting; this screen does not, and adding the dependency here would have settled that question
for the whole product on the strength of one strip.

## Security and permission impact

- **No write route to `spectrum_reservation` exists for any role** (§13.11).
  `default_permissions = ("view",)` so Django never generates add/change/delete, and
  `test_no_role_holds_add_change_or_delete_on_a_reservation` asserts the generated set is
  exactly `{view_spectrumreservation}` — because the line that prevents them is one line, and
  deleting it would silently create three permissions somebody could grant.
- Reservations are written only by `spectrum.services`, inside the transaction that writes the
  allocation they belong to.
- The spectrum view is **read for every role**, scope-filtered through the same Beam queryset
  every other Beam screen uses, so an out-of-scope Beam is a 404 rather than a 403 (**A-17**).

## Tests added

771 total, up from 697. 74 new.

| File | Covers |
|---|---|
| `test_exclusion.py` (17) | Overlap refused; guards compared, not occupied ranges; adjacency accepted; **reuse permitted across resources and refused on a shared one**; time as half the key; non-reserving statuses; fixed reserves excluding allocations; six CHECKs; no write permission exists |
| `test_concurrency.py` (4) | Two connections, exactly one row; the disjoint control; the deadlock translation; `IMMEDIATE` reporting on the statement |
| `test_capacity.py` (14) | Capacity through the ORM; another Beam's reservation on a shared resource counting against this one; bounded by assignment not window; two assignments as two pools; an expired assignment entitling nothing; revision excluding itself; every role may read |
| `test_reserve.py` (13) | One row per resource per leg; **all-or-nothing rollback across resources**; `reserves_spectrum` derived; OQ-08 both ways; time containment refused with the assignment named; auditing; release |
| `test_gaps.py` (24) | Subtraction, clipping, sorting, overlapping inputs; gaps never merged across assignments; neighbours reported; first-fit determinism; and seven Hypothesis properties — no free part overlaps anything occupied, none escapes its entitlement, none is empty |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.11 — spectrum view and free capacity | **Met.** Per direction and leg, with gaps, largest gap and utilisation. |
| §26.14 — the overlap guarantee | **Met**, at the database, under concurrency, with no write route to bypass. |
| §26.15 — free capacity is calculated | **Met.** Nothing is stored; ADR-0009 records why. |
| §26.16 — calculated values are engine-owned | **Held.** The gap engine is pure and Django-free. |
| §26.20 — no invented RF value | **Held.** No reservation is seeded, and none could be. |

## Verification performed

```
pytest                                   771 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (9 modules, calculations strict)    no issues in 109 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

The concurrency test was run twenty consecutive times after the fix.

## Remaining open questions

**OQ-08** now has a real home: `reserves_spectrum` is written from one setting in one function,
the CHECK pins the seven statuses whose policy is fixed, and `SUSPENDED` is deliberately absent
from it (ADR-0017). Answering "retain" is a no-op; answering "release" is a settings change plus
a tightened CHECK.

**OQ-24** (fixed reserved areas) has its container and its constraint, and ships empty —
`kind=FIXED_RESERVE` works and is tested, but no row is seeded.

**OQ-32** is the one to settle before S11, and it grew teeth in S9a: an allocation must sit
inside its assignment's period as well as its Satnet's and Beam's. The service enforces it and
names the assignment in the refusal; whether it should be a hard rule or a warning is still
open.

**OQ-34** (are minimum edge guards part of the allocated range) is now sharper than when it was
recorded. `ck_res_within_assignment` compares the **allocated** range, so a transmission needing
a guard at the edge of its assignment needs a wider assignment rather than permission to reserve
beyond it. That is the conservative reading and it is enforced; if the answer is the other one,
the CHECK changes.

## Next slice

**S10 — Satnets.** The first real scope enforcement on a write path: Beam **and** Hub must both
be in scope (**A-17**). Its capacity summary reads the selector built here, so the number an
operator sees when choosing a Satnet is the same number the constraint will enforce against —
which is the property that makes a capacity display worth showing at all.
