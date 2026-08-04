# Slice S10 — Satnets

**Phase:** 5
**Report format:** Root Specification §27

---

## Goal

An operator creates and manages Satnets **under authorised Beams**, and sees a capacity
summary computed from the reservation table built in S9.

This is the slice where object scope stops being a design note and starts refusing requests.

## Files created or changed

**satnets** (new) — `models.py`, `constants.py`, `scope.py`, `services.py`, `selectors.py`,
`forms.py`, `views.py`, `urls.py`, `apps.py`, `migrations/0001_initial.py`,
`migrations/0002_satnet_hub_gateway_composite_key.py`

**accounts** — `UserBeamScope`, two capabilities, `migrations/0009` and `0010`

**Interface** — `templates/satnets/` (list, detail, form, the scope-denied page), navigation

**Tests** — `tests/satnets/{test_scope,test_lifecycle}.py` (30)

**Tooling** — `satnets` added to the import-linter layers and the `types` gate

## Database impact

| Table | Notes |
|---|---|
| `satnet` | One Beam, one Hub, a denormalised Gateway, a validity period |
| `user_beam_scope` | Planned in S2, deferred until the Beam existed to point at |

- **`uq_satnet_beam_code`** — codes unique per Beam (**A-18**).
- **`uq_satnet_id_beam`** — the target S11's Satnet Path composite key will need. Created now
  so that migration does not alter a populated table, the same reason S4 created
  `uq_hub_id_gateway` for this one.
- **`fk_satnet_hub_gateway`** — the composite key pinning the denormalised Gateway to the
  Hub's. Without it a Satnet could name a Gateway its Hub is not at, and **every scope check
  that trusted the copy would answer about the wrong site — in the direction that grants
  access**.

## The conjunction, and why it is two functions

**A-17**: *"acting on a Satnet requires the Beam **and** the Hub in scope."* §25: *"Operator
can create Satnet only under authorized Beam."*

`UserBeamScope` was planned in S2 and deferred because Beam did not exist yet. Until this slice
that sentence in §25 had nothing to check against.

**Reading and acting are separate questions, and they are separate functions.**
`accounts.policy.require` runs one resolver for *every* capability on a model, so a resolver
that answered the write question would silently deny reads — the exact bug S8's `beams/scope.py`
had, caught by `test_an_operator_may_validate_a_beam`. So:

- `satnet_in_scope` answers the **read** question, and answers yes for any authenticated user.
- `scope.may_act_on` answers the **write** question, requires both grants, and returns *the
  reason* alongside the answer.

**Reading stays open deliberately.** An operator has to see the fleet before asking for access
to part of it, and a list that hid everything ungranted would look like missing data rather than
a boundary. The list marks which rows are actionable instead.

**The refusal names the missing grant.** "Denied" alone sends somebody to the wrong person — a
Beam grant and a Hub grant are often requested from different people. A capability denial and a
scope denial are also kept distinct: *"your role cannot do this"* and *"your role can, but not
here"* have different remedies, and `test_a_capability_denial_and_a_scope_denial_are_different_refusals`
pins the difference.

**Direct POST is the guarantee; the narrowed form is a courtesy.** The create form filters its
Beam and Hub choices to granted objects, and the service checks again on save.
`test_posting_an_out_of_scope_beam_directly_is_refused` is the one that matters — without it
the whole rule would rest on a queryset filter that any second entry point bypasses.

## A Satnet is never re-parented

`beam` and `hub` are set at creation and refused on update, by the service *and* by absence
from the edit form.

Moving a Satnet to another Beam would change which spectrum resources the allocations
underneath it compete on (ADR-0018) **without touching those allocations**. Every reservation
would silently start being judged against a different pool — no error, no migration, no trace.
Changing Beam means a new Satnet.

The service refuses it rather than relying on the form, because a service is reached by more
than one form: S15's importer is the next caller.

## Capacity is the Beam's, and it is computed

§16 and ADR-0009. Nothing about capacity is stored — `test_nothing_about_capacity_is_stored`
asserts no such column exists, because the day one appears it becomes a second source of truth
for free capacity and the failure is silent.

**A Satnet's capacity is its Beam's, not a share of it.** A Satnet holds no spectrum of its
own, and the allocations under it compete with everything else on the same resources —
including other Satnets', and other Beams' entirely (ADR-0018). Presenting a per-Satnet figure
would be a subset shown as a whole.
`test_capacity_counts_allocations_belonging_to_other_satnets` pins it, and
`test_capacity_matches_a_hand_computed_figure` is §26.8's hand-computed fixture: 100 MHz
entitlement, 12 MHz held including guards, 88 MHz free.

## Deactivation, and what it does not do

Deactivating a Satnet is **never blocked by existing allocations**, unlike inventory
deactivation, which refuses while dependants exist. The two are different acts: deactivating a
Frequency Window orphans records that depend on its engineering values, while deactivating a
Satnet is a decision about *future* work. Its live allocations keep their spectrum until they
are retired individually.

What it stops is new Satnet Paths, and so does a deactivated Beam — §13.9's "a Satnet cannot
outlive its Beam", expressed as `Satnet.accepts_new_paths` rather than as a constraint.
Deactivating a Beam does not rewrite every Satnet under it, and a stored copy of the Beam's
state would be one more thing that can disagree.

## Security and permission impact

- New capabilities: `satnets.view_satnet` (all roles — an Approver reviewing an allocation
  needs the context), `satnets.manage_satnets` (**Admin and Operator**).
- Operator holding the capability is *not* sufficient: object scope is checked separately and
  refuses with the missing grant named.
- Administrators bypass scope (**A-17**), checked first so they are never told about a grant
  that does not apply to them.
- Scope denials are audited (§18) — the denial an administrator most often has to explain,
  because the role is right and the screen was reachable, so it looks like a bug to whoever
  hit it.

## Tests added

809 total, up from 771. 38 new.

| File | Covers |
|---|---|
| `test_scope.py` (15) | Both grants required; each missing one named; **Gateway cascades to Hubs and a Hub grant does not imply its Gateway** (OQ-30); admin bypass; denial audited; reading open while acting is not; **direct POST refused**; capability and scope denials distinguished |
| `test_lifecycle.py` (15) | Gateway derived and unable to lie; code unique per Beam; effective period; **never re-parented**, by service and by form; edit audited with the replaced values; inactive Satnet and inactive Beam both stop new paths; deactivation never blocked; capacity hand-computed; capacity counts other Satnets'; nothing stored; selectable requires both grants and active |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.8 — Satnets under authorised Beams, with a capacity summary | **Met**, with the summary computed from the S9 selector rather than a parallel one. |
| §26.16 — calculated values are engine-owned | **Held.** No capacity column exists. |
| §26.17 — auditing | **Held.** Create, update, activation and scope denials. |
| §26.20 — no invented RF value | **Held.** No Satnet is seeded; none could be, since none of its ancestors are. |

## Verification performed

```
pytest                                   809 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (10 modules, calculations strict)   no issues in 119 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## Remaining open questions

**OQ-30 is answered by construction and tested**: scope is conjunctive, a Gateway grant
cascades to its Hubs, and a Hub grant does not imply its Gateway. Recorded as **A-17** since the
design pass; this is the first slice where the answer had consequences that could be tested.

**OQ-21** (required service, customer and platform metadata) is untouched: the Satnet carries
the three fields §13.9 names and nothing beyond them. Inventing columns would produce a shape
somebody has to migrate away from.

**OQ-32** is still the one to settle before S11, and it is now three periods deep — a Satnet
Path must sit inside its Satnet's validity, its Beam's, *and* its spectrum assignment's.

## Next slice

**S11 — Guided Satnet Path creation.** The §9 workflow end to end, and the first writer of
reservations. Three things it inherits and must not lose: `selectable()` offers only Satnets
whose grants are held and whose Beam is active; `SpectrumConflictError` is what a collision
raises, in both its shapes; and the §9.5 blocking message must name **which resource**
conflicted, because an allocation occupies several.
