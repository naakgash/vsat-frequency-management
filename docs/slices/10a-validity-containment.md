# Slice S10a — Validity containment

**Phase:** 5
**Report format:** Root Specification §27
**Answers:** **OQ-32**

---

## Goal

Implement the containment rule the OQ-32 answer specifies, so S11's wizard has something exact
to refuse with — and close the gap the answer exposed.

## What the answer required

Three hard containment rules for an operational Satnet Path: its period must sit inside its
**Satnet's**, its **Beam's**, and its **Beam Spectrum Assignment's**. The maximum permitted
period is their intersection; the service must **name the limiting parent** and **return that
maximum**. Drafts may sit outside and warn, but may not reserve spectrum or become operational.

And the sentence that is easy to skim past:

> *"The referenced Spectrum Assignment must also belong to the same Beam and be compatible with
> the Satnet Path's direction, polarization and Payload Path. **Temporal containment alone is
> not sufficient.**"*

## The gap it exposed

**The Beam had no validity period.** It carried `is_active`, `activated_at` and `activated_by`
and nothing temporal at all — so "contained within its Beam's validity period" had nothing to
check against.

`docs/design/02` §1 lists Beam among the `EffectiveDated` entities and `docs/design/04` §4 names
a `ck_beam_effective` constraint. The design expected it from the first pass; S8 did not build
it, and nothing needed it badly enough for the omission to surface.

That is the second RF answer to find a gap rather than fill one. OQ-26 ruled out a foreign key
the platform would have added; this one required a column it had quietly skipped. Both were
found by asking a question whose answer was expected to be routine.

## Files created or changed

**calculations** — `periods.py` (pure, added to the purity contract)

**beams** — `Beam.effective_from` / `effective_until`, `ck_beam_effective_period`,
`Beam.validity`, `BeamSpectrumAssignment.validity`, `migrations/0004`

**satnets** — `containment.py`, `Satnet.validity`

**Documentation** — ADR-0020, the verbatim answer at
`docs/rf-confirmation/answers-oq-32.md`, register updates (**A-25**, OQ-32 answered)

**Tests** — `tests/domain/test_periods.py` (18), `tests/satnets/test_containment.py` (23)

## Database impact

| Table | Change |
|---|---|
| `beam` | `effective_from` (default now), `effective_until` (nullable), `ck_beam_effective_period` |

**Validity and activation are separate, and both bind.** `is_active` is a switch somebody flips
now; validity is the span over which the Beam is a real thing. Collapsing them would make
deactivating a Beam retroactively invalidate allocations that were legitimate when made.

Existing Beams default to valid-from-creation, open-ended, so behaviour is unchanged until
somebody sets an end date — at which point the column starts refusing allocations that used to
be accepted, which is the point of it.

## Three things worth reviewing

**`intersect` returns `None`, not an empty period, when the parents never overlap.** *"There is
no moment when all three are valid"* is a different statement from *"the window is zero wide"*,
and only the first is true when a Beam expires before its Satnet begins. A caller handed an
empty period would offer it to an operator as a maximum.

**A bounded parent does not contain an open-ended child.** This is the case that will happen
most: somebody leaves the end date blank meaning "until further notice", under a Beam that
expires in March. Unchecked, the allocation outlives the Beam it depends on by simply not saying
when it stops. The asymmetry lives in one place in `calculations.periods` rather than in a
condition every caller has to remember.

**Draft and operational are the same rules and different verdicts.** `evaluate` takes an
`operational` flag that decides *severity* and never *rules*.
`test_the_same_facts_produce_the_same_findings_for_a_draft` pins it: two code paths would
eventually disagree about what "contained" means, and the one that drifts unnoticed is the
strict one, because drafts are what people exercise daily.

## A stated gap

**Containment is not a database constraint, and cannot be.** It spans four tables and a CHECK is
per-row. `spectrum.services` still enforces the assignment period on the reservation itself,
which is the last line, but the three-way rule is service-level.

That is a real departure from the rest of the product's *"the database is the final authority"*
posture, and it is recorded here and in ADR-0020 rather than left to be discovered: a direct SQL
insert can create a Path outside its Satnet's period. The alternative is a trigger, which would
put the rule somewhere no reader of the model would find it.

## Tests added

850 total, up from 809. 41 new.

| File | Covers |
|---|---|
| `test_periods.py` (18) | Half-open containment at the upper edge; open-ended parents and children, including the asymmetry; intersection as latest-start/earliest-end; touching periods intersecting to nothing; empty input refused rather than defaulting to unbounded; four Hypothesis properties, including **the intersection is contained by every member** |
| `test_containment.py` (23) | Each of the three parents as the limiting one, named; the maximum returned; the message stating cause and maximum; open-ended request under a bounded parent; no-common-period reported as such; draft and operational producing identical findings; **all five compatibility checks**, including one that passes temporally and still blocks; the Beam's new validity period, its CHECK, and its independence from activation; one revision per assignment, and a Path spanning two refused |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.9 / §26.13 — validation with actionable messages | **Advanced.** The refusal names the limiting parent and the maximum permitted period, which is what §9.5 asks of the blocking message. |
| §26.16 — calculated values are engine-owned | **Held.** The interval arithmetic is pure and Django-free. |
| §26.20 — no invented RF value | **Held.** No dates are seeded; a Beam without them is open-ended. |

## Verification performed

```
pytest                                   850 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (10 modules, calculations strict)   no issues in 121 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## Remaining open questions

**OQ-32 is closed.** The transcript is at `docs/rf-confirmation/answers-oq-32.md`; the rule is
**A-25** and ADR-0020.

The answer's software-defined-payload paragraph — *"the operational configuration shall be
represented by time-bounded Satnet Path revisions or activations… a new revision is required
when the assignment, Beam or relevant payload configuration changes"* — is **S12's** work.
S10a implements the constraint each revision must satisfy; S12 builds the revision workflow that
creates a successor when an assignment ends. `test_a_path_spanning_two_assignments_is_refused`
is the refusal that will send an operator there.

**OQ-22 remains the one gap that cannot be closed by building.** Everything in `tests/domain/`
proves the engine self-consistent; nothing proves it agrees with RF engineering's own figures.
It becomes a hard failure at Phase 9.

## Next slice

**S11 — Guided Satnet Path creation.** It now has three things ready for it: `selectable()`
offering only Satnets whose grants are held, `SpectrumConflictError` for a collision in both its
shapes, and `containment.evaluate` for a period refusal that names the parent and the maximum.
