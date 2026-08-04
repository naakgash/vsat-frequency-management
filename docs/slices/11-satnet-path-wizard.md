# Slice S11 — Guided Satnet Path creation

**Phase:** 5
**Report format:** Root Specification §27

---

## Goal

The §9 workflow end to end: a sizing request, a centre frequency or Auto-place, a live
engineering preview, and a validated save that writes the allocation and every reservation
holding its spectrum in one transaction.

This is the slice everything since S6 was built for, and the first writer of reservations.

## Files created or changed

**satnet_paths** (new) — `models.py`, `constants.py`, `services.py`, `selectors.py`, `scope.py`,
`forms.py`, `views.py`, `urls.py`, `apps.py`, `migrations/0001` and `0002`

**accounts** — two capabilities, `migrations/0011`

**Interface** — `templates/satnet_paths/` (list, detail, the create screen, `_findings`),
navigation

**Tests** — `tests/satnet_paths/{test_allocation,test_wizard}.py` (34)

**Documentation** — ADR-0005, this report

## Database impact

| Table | Notes |
|---|---|
| `satnet_path` | What was asked for, what was computed, and both sides as at save time |

**Twelve CHECK constraints**, being §20's list applied to both sides at once — centre inside
occupied, occupied inside allocated, roll-off in `[0,1]`, guards non-negative, allocated ≥
occupied, legs matching direction (**A-03**), and `ck_path_revision` requiring that revision 1
supersedes nothing and every later revision supersedes something.

**`fk_path_satnet_beam`** pins the denormalised Beam to the Satnet's. Without it a Path could
name a Beam its Satnet is not under, and the allocation would be judged against **another
Beam's spectrum resources** — the constraint enforcing faithfully against the wrong pool, which
is the worst kind of wrong: no error, and no overlap reported where one exists.

## The three things this slice is really about

**The server repeats every check on save** (§9.5). `preview()` is one function, called by the
live preview, by Auto-place's candidate check, and by `create()` immediately before writing —
because a preview computed by different code from the one that saves is a preview of something
else. `test_a_proposal_accepted_after_the_gap_was_taken_is_refused_on_save` is the case: the
proposal was correct when computed and wrong by the time it was submitted.

**Both legs block** (§8.2). A translated-side-only collision is the one an operator cannot see
— they choose an uplink centre that is genuinely clear, and the downlink image lands on
somebody else's transmission. Checking only the entered side would accept it.

**The refusal is a screen, not a sentence** (§9.5). `PathBlockedError` carries structured
findings: rule, Beam, Window, proposed range, each conflicting allocation with its range, the
overlap in Hz, the period in which both are valid, and the free gaps on that leg. Flattening
that to a string early throws away everything the screen needs, and a refusal without somewhere
else to go is a dead end — the operator's next question is always "then where".

## A-23, not "exactly two rows"

`docs/design/02` §5 says *"exactly two rows per Satnet Path — one per leg"*. The OQ-25 answer
superseded that: *"an allocation may reserve more than one spectrum resource."*

`_occupancy` writes one row **per resource per leg**, and
`test_saving_writes_a_reservation_per_resource_per_leg` puts a second RF chain on the uplink
and asserts three rows. Anything assuming a pair would break the first time a leg shares two
chains, silently, by reserving on only one of them.

## §26.16 — derived values are system-owned

The form's field list *is* the guarantee: code, direction, input mode and value, roll-off,
guard policy, centre, period, and the two hardware strings. Every computed value is absent, so
no role can bind one.

Two tests, deliberately at different levels: one asserts the field list contains no derived
name — so adding one fails there rather than silently handing an operator control of the
engine's output — and one POSTs derived values over HTTP and checks the stored row ignored them.

## A deliberate deviation from the plan

The plan calls for the wizard as **HTMX fragments, one per step**. This ships a single form with
a live preview beside it.

The wizard's value is the *preview*, not the pagination. A multi-step flow holding half an
allocation in the session is a second place for that allocation to be wrong, and the reason §9
describes steps is to get the operator to a preview before they commit — which one page does
directly. The preview and the save call the same function, so what is shown is what is checked.
HTMX remains vendored and the step boundaries are still visible in the form's grouping; if the
flow proves too dense in the pilot, splitting it is a template change rather than a redesign.

## An edge the tests found

The create form uses `datetime-local`, which truncates to the minute. A `valid_from` of "now"
therefore lands up to 59 seconds *before* a Beam created seconds earlier — and the OQ-32
containment rule correctly refuses it, because a Beam is not valid before it exists.

The fixtures now commission master data a week before it is used, which is what actually
happens. The sharp edge is real and stays: `Beam.effective_from` defaults to *now*, not to the
start of the day, because "the start of which day" depends on the display time zone and that is
**OQ-23**, still unanswered. Rounding to a midnight would bake in a time zone assumption to
avoid a message that is telling the truth.

## Security and permission impact

- New capabilities: `satnet_paths.view_satnetpath` (all roles — an Approver decides on an
  allocation, an Observer reports on it), `satnet_paths.manage_satnet_paths` (Admin, Operator).
- **Object scope still applies** (**A-17**): the Satnet's Beam *and* Hub must both be granted,
  delegated to `satnets.scope.may_act_on` so the two records can never disagree about who may
  act on them. Tested by direct POST.
- A scope refusal returns **409 with findings**, not a form error: the submission is well-formed
  and was refused by a rule about the world, not by a field anybody can retype.
- Reservations are still written only by `spectrum.services`, inside this slice's transaction.

## Tests added

892 total, up from 850. 42 new.

| File | Covers |
|---|---|
| `test_allocation.py` (19) | Both sides stored with width preserved; **a row per resource per leg**; a draft reserving nothing; guards in the reserved range; both input modes agreeing; derived values ignored; the §9.5 message item by item; **a translated-side-only conflict**; a refusal writing nothing and being audited; entitlement and period refusals; Auto-place proposing, being deterministic, avoiding what is held, and returning nothing when nothing fits; the accepted-then-taken race; scope |
| `test_wizard.py` (15, incl. parametrisation) | Preview writing nothing; save creating Path and reservations; **409 not 400**; the blocking screen's content; Auto-place over HTTP and its no-fit message; the form binding no derived field; derived POST ignored end to end; capability and scope refusals; every role reading; the list showing only current revisions; a missing value as a field error |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.9 — guided creation with validation | **Met.** One screen, live preview, refusal with reasons. |
| §26.10 — calculated two-sided placement | **Met**, through the S6/S7 engine unchanged. |
| §26.11 — spectrum view and free capacity | **Met** through S9's selector, reused rather than reimplemented. |
| §26.12 — equipment and IF | **Partial.** The columns exist and are written when a profile is chosen; automatic profile matching lands with S12's lifecycle work. Stated rather than implied. |
| §26.13 — blocking message content | **Met.** Every item §9.5 lists is carried and rendered. |
| §26.16 — derived values are engine-owned | **Met**, tested at the form and over HTTP. |
| §26.20 — no invented RF value | **Held.** Nothing is seeded. |

## Verification performed

```
pytest                                   892 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (11 modules, calculations strict)   no issues in 131 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

An existing guard rail earned its place: `test_every_designed_scoped_model_has_a_resolver`
failed the moment `SatnetPath` appeared, because the model was scoped in the design and had no
resolver. It was written in S2 against a list of models that did not exist yet.

## Remaining open questions

**OQ-09** and **OQ-10** — `gw_id` and `decimator` are validated free text. Modelling them as
exclusive `HardwareResource` records now would invent an exclusivity rule nobody has confirmed,
and the platform would start refusing allocations on it.

**OQ-31** — no tuning raster is enforced, so Auto-place may propose a centre no modem can tune
to. `Band.tuning_raster_hz` is the column; when it is filled, Auto-place rounds to it.

**OQ-23** — the display time zone. Newly consequential: see the edge above.

**OQ-22** — still the one gap that cannot be closed by building.

## Next slice

**S12 — Lifecycle, approvals and revisions.** The §15.2 transition graph, second-person
approval, and the `ON_AIR` revision that closes the old period *before* opening the new one
(**A-14**) — which is also where the OQ-32 answer's software-defined-payload paragraph lands:
a new revision when the assignment, Beam or payload configuration changes.
`test_a_path_spanning_two_assignments_is_refused` is the refusal that will send an operator
there.
