# Slice S6 — Calculation Engine: Bandwidth, Edges, Guards

**Phase:** 4, pulled early
**Report format:** Root Specification §27

---

## Goal

One engine, with no formula anywhere else in the product, and an Engineering Preview screen
that lets a user exercise it end to end before any Beam exists.

Pulled forward deliberately. The engine is pure and depends on dataclasses rather than
models, so the highest-risk logic in the platform can be built and property-tested before
there is a single row of inventory data to test it against.

## Files created or changed

**calculations** (new) — `rounding.py`, `ranges.py`, `types.py`, `bandwidth.py`,
`guards.py`, `validation.py`, `units.py` (moved from `inventory/`), plus the Django surface:
`forms.py`, `views.py`, `urls.py`, `apps.py`

**inventory** — `forms.py` and `templatetags/rf.py` now import `calculations.units`

**Interface** — `templates/calculations/preview.html`, navigation entry

**Tests** — `tests/domain/{test_ranges,test_bandwidth,test_guards,test_validation,test_preview}.py`,
and `test_units.py` moved from `tests/inventory/`

**Documentation** — `docs/adr/0008-half-open-ranges.md`,
`docs/adr/0010-central-calculation-engine.md`,
`docs/adr/0016-guard-policy-hierarchy.md`, this report

**Tooling** — a fifth import-linter contract; `mypy` and CI extended to the new module;
the Trivy step rewritten (see below)

## Database impact

**None.** The engine is pure and this slice adds no model, no migration and no column.

## The rounding policy

§14.3 requires *"one documented rounding policy"* and does not state one. **A-09** adopts
**outward** rounding, and this slice implements it in `calculations/rounding.py`:

| Quantity | Direction | Why |
|---|---|---|
| Occupied bandwidth | **up** | Never under-state what a transmission uses |
| Half-width | **up** | An odd bandwidth widens rather than narrows |
| Guard widths | **up** | A guard is a *minimum* separation |
| Derived symbol rate | **down** | Never over-state what a transmission can carry |

Each errs towards the answer that cannot cause a collision or an over-claim. The forward and
reverse directions are therefore **not exact inverses**: a round trip returns a symbol rate
at most one symbol/second below the original, and that bound is stated as a test rather than
left to be discovered.

The module draws a distinction worth naming. **Policy rounding** is deliberate and visible
at the call site — `ceil_hz`, `floor_hz`. **Accidental rounding** is a defect: a
multiplication that silently loses digits because a `Decimal` context ran out of precision,
producing a number that is nearly right. `rounding.EXACT` traps `Inexact`, so arithmetic
that must be exact *raises* instead of approximating. Division is inherently inexact and
uses a non-trapping context, then hands its result to an explicit ceiling or floor. Keeping
the two apart is what stops a stray digit masquerading as the policy.

**OQ-29** remains open: outward rounding is a *policy*, not a measurement, and it needs RF
sign-off because it must match how the incumbent spreadsheets round or the Phase 9 migration
comparison will show differences that are not real. Changing it is one module.

## Security and permission impact

- The Engineering Preview is **read-only and saves nothing** — no model, no audit record, no
  session state. It requires sign-in and nothing more, because there is no data to scope
  and nothing to audit. `test_the_preview_writes_nothing` asserts it directly.
- Every number on the page comes from the form that was just submitted, so the screen
  discloses nothing about the estate.
- `tests/permissions/test_url_coverage.py` continues to pass: the view declares
  `LoginRequiredMixin`.

## Purity, enforced rather than asserted

`docs/design/01` says `calculations/` *"imports nothing from Django models"*. This slice
turns that from prose into a contract, and made it stricter: the engine may not import
**Django at all**.

```
name = "the calculation engine is pure"
source_modules   = calculations.{bandwidth,guards,ranges,rounding,types,units,validation}
forbidden_modules = django, config, accounts, audit, inventory, operations, specifications
```

Django is on the list deliberately. Importing `django.conf.settings` would make the
arithmetic depend on configuration, and a rounding policy that varies by environment is not
a policy. `calculations/{forms,views,urls,apps}.py` are the Django surface *over* the engine
and sit outside the contract.

The contract was verified by temporarily adding `from django.conf import settings` to
`calculations/ranges.py` and confirming it went **BROKEN**, then reverting. A contract nobody
has watched fail is a contract nobody knows is wired up.

`calculations` was also inserted into the layer order, between `specifications` and
`accounts`, matching the target graph in `docs/design/01`.

## `inventory/units.py` moved to `calculations/units.py`

Unit conversion belongs to the lowest layer of the engineering path: the forms, the display
filters and the engine all need it, and `calculations` is the only module every one of them
may import. The move is why ADR-0003's three call sites now read
`calculations.units` — the arithmetic is unchanged and `tests/domain/test_units.py` moved
with it.

## Tests added

480 tests total, up from 382. 98 new, of which **31 are Hypothesis properties**:

| File | Covers |
|---|---|
| `test_ranges.py` (26) | The half-open boundary in both directions; touching ranges do not overlap; an empty or float-edged range is refused; **reflection preserves width and is its own inverse**; overlap is symmetric; expanding always contains the original |
| `test_bandwidth.py` (26) | Both §9.2 entry modes; supplying both or neither is refused; rounding up and down at the documented places; a percentage entered as a roll-off is refused; **occupied bandwidth is never below the exact product and never a whole Hz above it**; the centre is always recoverable; occupied is always inside allocated; monotonic in both symbol rate and roll-off |
| `test_guards.py` (18) | Each mode; the combined mode takes whichever is wider; **the full ADR-0016 hierarchy, level by level**; the source is reported; **the engine's `GuardMode` matches the database's** |
| `test_validation.py` (16) | Window containment refuses, band limits only warn, edge guard only warns (**OQ-34**); a placement ending exactly at a Window edge fits and one Hz past does not; every problem reported at once; every finding cites its rule |
| `test_preview.py` (13) | Both entry modes over HTTP; the unused field ignored rather than combined; guard and window validation; **the formula shown beside every derived value** (§9.4); the screen writes nothing |
| `test_units.py` (18) | Moved unchanged from `tests/inventory/` |

The property tests are the reason this slice was pulled forward. The formulas are small
enough to look obviously right and subtle enough to be wrong at the edges — an odd
bandwidth, a roll-off that does not divide evenly, a value above the 32-bit boundary.
Examples catch what someone thought of.

One property earns particular mention. `test_the_centre_is_always_recoverable` holds because
the occupied range is built from a half-width rounded up, making its width even — so the
midpoint is exact for *every* bandwidth, odd or even. That is a consequence of A-09 rather
than an independent design choice, and it is what lets a stored allocation be re-displayed
without drift.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.10 — derived values calculated by the platform | **Partially met.** The engine computes bandwidth, edges and guards; translation, IF and equipment matching are S7. |
| §26.16 — calculated values are engine-owned | **Met for this slice's values.** One package, one entry point, and an import contract that keeps a second implementation out. |
| §26.18 | **Partial**: ADR-0008, ADR-0010 and ADR-0016 added; runbooks remain S17. |
| §26.20 — no invented RF values | **Held.** The engine ships no guard value, no roll-off default and no window. |

## Verification performed

```
pytest                                   480 passed
pytest tests/domain                      116 passed (31 Hypothesis properties)
pytest -m browser                        8 passed
ruff check . / ruff format --check .     clean
mypy (7 modules, calculations strict)    no issues in 83 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

The purity contract was additionally verified to *fail* when violated, as described above.

## What was deliberately not invented

**No guard value, no roll-off default, no window.** The engine has three guard modes and no
policy; §9.2's two entry modes and no pre-selected default (**OQ-05**); roll-off validation
and no per-platform value (**OQ-06**).

Three restraints worth flagging:

- **`NO_GUARD` is zero, and zero is reported.** Inventing a plausible 250 kHz would be
  indistinguishable from a confirmed value once it reached an allocation. But an unguarded
  placement is more often a missing policy than a decision, so `validation` raises
  `NO_GUARD_APPLIED` — a **warning**, because adjacency is legal (§25) and blocking would
  make the platform unusable while **OQ-07** is open.
- **The edge-guard check warns rather than refuses.** **OQ-34** asks whether the minimum
  edge guard is part of the allocated range or a separate standoff. Making it an error would
  answer that by implication, in code nobody would think to revisit.
- **Band limits warn; Window limits refuse.** That asymmetry is §13.2's, not a hedge: the
  Window authorises the allocation and the Band describes the band. Refusing on a Band would
  let an out-of-date record block spectrum a Window explicitly grants.

## A CI failure fixed alongside

`aquasecurity/trivy-action@0.28.0` stopped resolving — *"unable to find version"* — failing
the security job in three seconds, before the scanner was ever reached. A gate that breaks
when a third party retags is not a gate.

Trivy now runs from its own published image, at a version pinned in a file we control and
can verify, which also removes a third-party action from a pipeline that holds a repository
token. The vulnerability database is cached: it comes from ghcr.io, which rate-limits
anonymous requests, and GitHub-hosted runners share source addresses — so an uncached scan
can fail on traffic that is not ours.

## Remaining open questions

Touched, not resolved: **OQ-05** (which entry mode is pre-selected — both are implemented),
**OQ-06** (roll-off default by platform), **OQ-07** (guard values), **OQ-29** (the rounding
policy needs sign-off), **OQ-34** (edge guard semantics).

**Unchanged and still required before S9:** **OQ-25**, **OQ-26**, **OQ-27**.

## Next slice

**S7 — Translation, IF and equipment matching.** The payload translation the engine can
already express as a shift or a reflection, applied through a `PayloadPath`; RF↔IF conversion
through an `EquipmentProfile`, including the high-side injection case where the spectrum
inverts; and matching a placement to the profiles that can actually reach it. It extends this
engine rather than adding a second one — the inversion arithmetic and the 1 Hz edge that
moves with it are already here, tested, in `FrequencyRange.reflect`.
