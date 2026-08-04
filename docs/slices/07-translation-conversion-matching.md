# Slice S7 — Translation, IF Conversion, Equipment Matching

**Phase:** 4
**Report format:** Root Specification §27

---

## Goal

Given an allocation on one side of a payload, the engine produces the other side, the L-band
IF range, and the equipment profiles that can actually carry it — with the golden-example
harness wired up and empty, waiting on **OQ-22**.

## Files created or changed

**calculations** — `translation.py` (new), `conversion.py` (new), extended `types.py`
(`TwoSidedPlacement`), `bandwidth.py` (`place_both_sides`), `validation.py`
(`check_two_sided`, `check_translation`, `check_conversion`), `forms.py`, `views.py`

**Interface** — `templates/calculations/preview.html` now shows both sides of the payload

**Tests** — `tests/domain/{test_translation,test_conversion,test_two_sided,test_golden_examples}.py`,
`tests/domain/golden/` (empty, with a README), extended `test_preview.py`

**Documentation** — `docs/adr/0006-two-sided-reservations.md`, this report

**Tooling** — the purity contract extended to the two new engine modules

## Database impact

**None.** The engine remains pure and this slice adds no model, no migration and no column.

## The two-sided rule

A Satnet Path occupies spectrum in two places and §8.1 makes each exclusive. The decision
this slice makes — ADR-0006 — is that **one side is calculated and the other is its image**,
never a second independent calculation.

The reason is a single Hz. The occupied range is built from a half-width rounded outward
(**A-09**), so an odd bandwidth produces a range one Hz wider than the computed bandwidth.
Round that independently on each side and the two can differ — an allocation that fits its
uplink Window exactly would fail containment on the downlink, for reasons no screen could
explain. Moving the interval whole makes the far side inherit the near side's rounding
instead of repeating it.

Either side may be the entered one (**OQ-28**), and the pair is symmetric: entering the
uplink centre or the corresponding downlink centre produces identical results.

### Inversion cannot be derived, and that is worth stating

Translation preserves width, so **any** downlink interval reachable by a reflection is
equally reachable by a shift: given `[a, b)` and a same-width `[c, d)`, both `shift(c − a)`
and `reflect(c + b)` produce it. The pair of intervals therefore contains no evidence of
which happened.

`inverted` is consequently carried from the Payload Path rather than inferred from the
geometry. `test_inversion_is_carried_rather_than_derived` constructs two placements with
*identical* ranges that differ only in the flag, which is the demonstration that the
inference is impossible rather than merely awkward.

It matters downstream: under inversion the operator's low edge on one side is the high edge
on the other, and a plot drawn left-to-right on both sides shows the transmission mirrored.

### A contradiction that is reported, not resolved

The model stores `spectral_inversion` independently of the translation method. An offset
preserves frequency order and carries no reflection constant, so a path flagged as inverting
while using an offset cannot be computed either way. `check_translation` reports
`INVERSION_WITHOUT_REFLECTION` as an error rather than picking an interpretation that would
look plausible and be wrong.

## Why `sideband` is a separate field

§13.5 requires `IF = |RF − LO|`, which is **not invertible on its own**: an IF of 1 GHz with
an LO of 28 GHz could mean an RF of 27 or 29 GHz, and the absolute value does not say which.
`sideband` records which side the local oscillator sits on, and that is what makes the
conversion a function in both directions.

| Method | Sideband | Up (IF→RF) | Down (RF→IF) | Inverts |
|---|---|---|---|---|
| `LO_PLUS_IF` | `LOW_SIDE` | `RF = LO + IF` | `IF = RF − LO` | no |
| `LO_MINUS_IF` | `HIGH_SIDE` | `RF = LO − IF` | `IF = LO − RF` | **yes** |
| `FIXED_OFFSET` | either | `RF = IF + offset` | `IF = RF − offset` | no |

High-side injection is a **reflection through the LO**, not a shift — the same arithmetic as
an inverting payload translation, and the same one Hz of edge movement. The pairing is
enforced in `EquipmentProfileSpec.__post_init__` as well as by the database, because the
engine is reachable from the importer, which never sees `ck_equipment_conversion_sideband`
until commit time.

`FIXED_OFFSET` uses the profile's `lo_hz` as its offset. The model carries no separate
column, and adding one that is null for every other method would be worse than naming the
reuse explicitly — `EquipmentProfileSpec.translation_hz` is that name.

## Equipment matching

Selection is by RF containment, then IF containment, then `priority` — **never** by parsing
the label. §13.5 makes `LOW` / `MID` / `HIGH` free text, and a test asserts they change
nothing.

Two decisions worth naming:

- **Ties break on `code`.** Two profiles at priority 100 must not swap places between runs,
  or the same request would select different equipment on Tuesday than on Monday and nothing
  would explain it. A property test shuffles the input and requires the same output.
- **Rejected profiles are returned with their reasons.** §13.5 makes profile selection an
  operator-visible decision, and "no equipment matched" without saying why is not something
  anyone can act on. `best_profile` returns `None` rather than raising: no matching equipment
  is an ordinary outcome the interface has to present, not an error condition.

Containment is of the **whole interval**, not its centre. A transmission whose edge falls
outside a profile's range is not carried by it, however comfortably its centre sits inside.

## Tests added

551 tests total, up from 480. 71 new, of which **19 are Hypothesis properties**:

| File | Covers |
|---|---|
| `test_translation.py` (20) | All three methods; width preserved by each; a negative constant refused; **round-trip reversibility across every method**; entry from either side; the inversion flag, including the contradictory case; engine and database enumerations agree |
| `test_conversion.py` (30) | The method/sideband pairing refused both ways; low-side shift and high-side reflection; **high-side conversion is its own inverse**; negative and above-LO conversions refused with clear messages; RF-fits-IF-does-not; whole-interval containment; priority ordering; **deterministic tie-break**; the label proven inert; every rejection carries a reason |
| `test_two_sided.py` (15) | The downlink is the image of the uplink; both sides reserve the same width; the transmission itself unchanged between legs; entry from either side gives the same pair; **inversion carried rather than derived**; findings name their leg; a placement fitting one window and not the other refused |
| `test_golden_examples.py` (3) | The harness, the OQ-22 gate in both states |
| `test_preview.py` (+5) | Both sides over HTTP; a method without a constant refused; an inverting path shown as inverting; the contradictory path reported |

### The property that matters most

`test_translation_is_always_reversible` — `untranslate(translate(r)) == r` across every
method and thousands of intervals. ADR-0006's entire two-sided reservation rests on that one
claim, and the inverting case is where it is least obvious.

### A strategy bug caught while writing them

The first version of the conversion properties generated an RF range and an LO
independently and then used `assume()` to keep the valid pairs. The two sidebands constrain
the relationship in *opposite* directions — low-side needs the LO below the RF, high-side
above — so an independent pair is almost never valid for either. Hypothesis's
`filter_too_much` health check caught it: **0 inputs generated, 50 filtered**. The test would
otherwise have examined nothing while appearing to pass.

Rewritten to *construct* valid pairs — draw the LO, then build the RF interval at a drawn
spacing above or below it. No filtering, and the generated cases are all meaningful.

## The golden-example harness — OQ-22

`tests/domain/golden/` is wired up and **deliberately empty**.

Everything else in `tests/domain/` proves the engine is *self-consistent*: widths survive
translation, rounding goes outward, round trips return what they started with. **None of it
proves the answers are the ones RF engineering would give.** Only a worked example whose
numbers came from outside the codebase can do that (§24).

Writing a plausible example would be worse than having none — it would go green, look like
validation, and check the engine against itself with extra steps. §26.20 forbids exactly
this.

So the harness ships instead of the data. Drop a JSON file in the directory and it is
discovered and run; the format is in the test's docstring, and `source` — who supplied the
numbers, and when — is required, because an example whose provenance nobody can state is
indistinguishable from one somebody made up.

**The gate:** the test skips while the directory is empty and **fails** when
`VSAT_REQUIRE_GOLDEN_EXAMPLES=1` is set. Phase 9 sets it. That is what stops "we will add
them later" quietly becoming "we shipped without them".

Both states were verified, and so was the harness itself: a probe example was added,
confirmed to run and to *fail* when its expected values were altered by one Hz, then removed.
A harness nobody has exercised is one that breaks the day the real data arrives.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.10 — derived values calculated by the platform | **Met for the calculation half.** Bandwidth, edges, guards, translation and IF are all engine-owned. Auto-place and the gap engine are S9. |
| §26.12 — equipment compatibility | **Met for the calculation half.** Matching, ranking and the reasons for rejection. The selection *screen* arrives with the Satnet Path wizard in S11. |
| §26.16 — calculated values are engine-owned | **Held.** Two new modules, both inside the purity contract. |
| §26.20 — no invented RF values | **Held, and now enforced with a deadline.** The golden directory ships empty with a gate that fails at Phase 9. |

## Verification performed

```
pytest                                   551 passed, 2 skipped (the OQ-22 gate)
VSAT_REQUIRE_GOLDEN_EXAMPLES=1 pytest    fails, as designed
ruff check . / ruff format --check .     clean
mypy (7 modules, calculations strict)    no issues in 85 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## What was deliberately not invented

**No translation constant, no equipment profile, no golden example.** The shapes are here;
every number is an open question — translations **OQ-02**, equipment limits **OQ-04**, worked
examples **OQ-22**.

Two further restraints:

- **Remote-side equipment is out of scope.** **A-05** models hub-side conversion only, and
  **OQ-26** asks whether remote BUC/LNB profiles are needed. Adding a second profile
  reference now would be inventing a model for equipment nobody has confirmed exists in
  scope; it remains an additive migration if the answer is yes.
- **The preview checks the uplink Window only.** A downlink Window needs a second pair of
  fields, which belongs with the real Payload Path in S11 rather than on a sandbox. The
  two-sided *validator* accepts both and is tested with both.

## Remaining open questions

Touched, not resolved: **OQ-02**, **OQ-04**, **OQ-22** (now with a build gate), **OQ-26**,
**OQ-28** (both entry sides implemented; which is canonical per direction is unanswered).

**Unchanged and still required before S9:** **OQ-25**, **OQ-26**, **OQ-27**. S9 is the next
slice that cannot start without them — they determine the key of the central spectrum-overlap
exclusion constraint, and answering them afterwards means migrating a constraint-bearing
table with live data.

## Next slice

**S8 — Beam Builder.** The first entity built *on* the engine: a Beam is the root spectrum
pool, assembled from a Satellite, a Payload Path and the Frequency Windows either side of
it. It is also the last slice before the OQ-25/26/27 gate, so the Beam model is where those
answers land or the schedule stops.
