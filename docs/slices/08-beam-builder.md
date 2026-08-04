# Slice S8 — Beam and Beam Builder

**Phase:** 3
**Report format:** Root Specification §27

---

## Goal

An administrator builds a Beam through the guided wizard, sees both direction chains and the
rules that apply to them, and **cannot activate it while an enabled direction is invalid**
(§26.6).

This is the first entity built *on* the calculation engine, and the last slice before the
**OQ-25 / OQ-26 / OQ-27** gate.

## Files created or changed

**beams** (new) — `models.py`, `constants.py`, `validation.py`, `services.py`,
`selectors.py`, `scope.py`, `forms.py`, `views.py`, `urls.py`, `apps.py`,
`migrations/0001_initial.py`

**accounts** — two capabilities, `migrations/0007_reseed_beam_capabilities.py`

**Interface** — `templates/beams/` (list, detail, the five builder steps, and the
`_chain_diagram`, `_findings`, `_state_badge` and `_steps` partials), navigation entry

**Tests** — `tests/beams/{factories,test_validation,test_activation,test_permissions}.py`

**Documentation** — `docs/adr/0004-beam-root-pool.md`, this report

**Tooling** — `beams` added to the import-linter layers and to the `types` gate

## Database impact

| Table | Notes |
|---|---|
| `beam` | Identity, cached `configuration_state`, activation record |
| `beam_direction_config` | One row per `(beam, direction)`, both created with the Beam |
| `beam_direction_equipment_profile` | The candidate pool, with priorities |
| `beam_validation_result` | Append-only record of each run, with its findings |

Three constraints, and the reasoning behind where they are matters more than the list:

- **`ck_beam_active_requires_valid_configuration`** — §26.6's database half. An active Beam
  whose configuration is not `VALID` is unreachable by any route.
- **`ck_beam_active_has_activation_record`** — an active Beam must record who activated it
  and when. Without it an activation could be faked by flipping the boolean, and §18's trail
  would have nothing to show.
- **`ck_beam_direction_chain_all_or_nothing`** — a direction holds all three references or
  none. A path without windows is not partly finished; its windows disagree with its path by
  construction, which **A-06** forbids outright.

### A constraint that could not exist where it belonged

The rule I first wrote was "an enabled direction must be configured before its Beam may be
activated". Django rejected it: **`models.E041`**, `'constraints' refers to the joined field
'beam__configuration_state'`.

It is right to. The enabled flag lives on the child row and the activation on the parent, and
a CHECK constraint is per-row and cannot join.

So the database enforces the **consequence** on the Beam instead. That turns out to be the
stronger placement: `ck_beam_active_requires_valid_configuration` closes *every* route to the
forbidden state, including a direct SQL update or the S15 importer, where a constraint on the
child could only have caught one way of arriving there. `beams.validation` reports the
precondition with reasons; the Beam's CHECK makes the outcome impossible.

## §26.6, enforced three times

*A Beam cannot be activated while its mandatory FWD/RTN configuration is invalid.*

1. **`services.set_active` re-validates and refuses**, raising with the failing rules
   attached. A refusal that only says "no" leaves an administrator with nothing to fix.
2. **The database makes the state unreachable**, as above.
3. **The builder disables the button** — a convenience, and the screen says so explicitly:
   posting to the activation URL directly is refused too.

Only the first two are guarantees, and they are tested separately because each can fail on
its own.

**Activation re-validates rather than trusting the cached state.** `configuration_state` is a
cache so a list of fifty Beams renders fifty badges without running fifty validations; the
master data underneath a Beam can be superseded between the last run and the button press, so
a stored `VALID` is evidence of what was true earlier.
`test_activation_revalidates_rather_than_trusting_the_cached_state` breaks a Beam *without*
going through a service — exactly as a superseded window would — and checks the refusal.

**Refused activations are audited.** §26.6 makes the refusal a requirement, and a refusal
nobody can find afterwards is indistinguishable from a broken button.

## Which findings block, and which do not

The distinction is the slice's main judgement call, and it is drawn on one principle: a rule
whose answer is an open question **warns**; a rule the specification already settles
**blocks**.

| Blocks | Warns |
|---|---|
| Window is not the payload path's (**A-06**) | A direction is explicitly disabled (§5.4) |
| Payload path from another satellite | The payload path lists no polarization pairs (**OQ-03**) |
| Payload path running the other direction | No equipment profiles listed (**OQ-04**) |
| Polarization pair not permitted, or unset | |
| Canonical leg not part of the chain (**A-07**) | |
| Equipment from another band, or deactivated | |
| Every direction disabled | |

The two open-question warnings matter. Nothing is seeded for **OQ-03** or **OQ-04**, so
treating an empty list as "nothing is allowed" would make **every** Beam un-activatable until
an open question is answered — a worse failure than proceeding with the gap recorded. A
Satnet Path will refuse loudly in S11 when it finds nothing to convert through.

## Security and permission impact

- **Beam engineering is administrator-only** (§25). An Operator picks a Beam when creating a
  Satnet Path; they never configure one. Every builder route is tested by direct POST for
  three non-admin roles.
- **Reading is open to every role**, because that Operator has to be able to choose one.
- **Running a validation is a read.** Knowing whether a Beam is valid is part of reading it,
  and the check changes no configuration.
- New capabilities: `beams.view_beam` (all roles), `beams.manage_beams` (Admin).

### A scope resolver that denied the wrong thing

The first version of `beams/scope.py` answered `beam_in_scope` with
`actor.has_perm("beams.manage_beams")` — reasoning that Beam engineering is admin-only, so
non-admins are out of scope.

That is wrong, and `test_an_operator_may_validate_a_beam` caught it.
`accounts.policy.require` checks the capability **first** and *then* asks the resolver
whether this object is in scope. The same resolver runs for **every** capability on the
model, so gating it on `manage_beams` silently denied an Operator the *read* their
capability grants.

Scope is not capability. The resolver now answers the question it was asked — is this Beam
within the actor's object scope — and the answer is yes for any authenticated user, because
Beam-level scope *grants* land with the Satnet Path wizard in S11 and there is nothing to
narrow by until they exist. **A-17**'s deny-by-default is carried by the capability matrix
here, which is where §25's rule actually lives.

## Tests added

621 tests total, up from 551. 70 new:

| File | Covers |
|---|---|
| `test_validation.py` (22) | The three configuration states and their precedence; **§5.4's explicitly-disabled direction is valid and is shown**; **A-06** window identity in both directions, with the finding citing OQ-27; payload path from the wrong satellite or the wrong direction; canonical leg outside its chain; polarization pairs, including the OQ-03 empty case warning rather than blocking; equipment band and deactivation; every problem reported at once; every finding citing its rule; findings serialise to plain JSON |
| `test_activation.py` (16) | Valid activates, incomplete and invalid do not; the refusal names the failing rule and is audited; **activation re-validates rather than trusting the cache**; the run that justified an activation is kept; both database CHECKs; deactivation keeps the activation record and is never blocked; an Operator cannot activate but may validate; only active-and-valid Beams are selectable |
| `test_permissions.py` (32, incl. parametrisation) | Read for all four roles, sign-in required; **no non-admin can reach any builder route or POST to one**; denials audited; activation over HTTP refused with reasons (409) and permitted when valid; the wizard hands step to step; an unknown direction is 404 |

### A URL-ordering trap, for the second time

`/beams/<pk>/build/validate/` and `/build/activate/` were being captured by
`/build/<str:direction>/`, which sits in the same position. Three tests failed with 404s and
403s that had nothing to do with permissions.

This is the same trap S5 hit with the inventory activation route, and it now carries a
comment saying so in both places. A named segment and a free one in the same position always
resolve top-down; the specific routes have to come first.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.6 — a Beam cannot be activated while an enabled direction is invalid | **Met**, three times over, with the two enforcement layers tested separately. |
| §26.7 — the Beam Builder is a guided flow | **Met.** Five steps, each narrowing the next; the windows are not offered because choosing the path fixes them. |
| §26.16 — calculated values are engine-owned | **Held.** The Beam stores configuration, never a derived frequency. |
| §26.20 — no invented RF values | **Held.** A Beam is built from Windows and Payload Paths, and none of those are seeded either. |

## Verification performed

```
pytest                                   621 passed, 2 skipped (the OQ-22 gate)
ruff check . / ruff format --check .     clean
mypy (8 modules, calculations strict)    no issues in 96 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

mypy earned its place twice this slice: it caught `_check_*` dereferencing a nullable
`payload_path` that only `is_configured` guarantees, and — more usefully — it caught the
annotation `enabled_directions` **shadowing a model property of the same name**. Django
silently lets an annotation override a property, so the list template would have rendered a
count where any reader of the model would expect rows. Renamed to `enabled_direction_count`.

## What was deliberately not invented

**No Beam is seeded**, and none could be: a Beam is built from Frequency Windows and Payload
Paths, and neither of those is seeded either (**OQ-01**, **OQ-02**).

Two restraints worth flagging:

- **Windows must be identical to the payload path's, not contained in them.** Narrowing a
  Beam to a sub-range of a shared transponder is **OQ-27**. Enforcing identity now means that
  answer arrives as a feature rather than as a silent behaviour change, and the finding cites
  the open question so nobody mistakes it for a bug.
- **The canonical leg defaults to uplink in both directions** (**A-07**) and is *stored per
  direction*, so **OQ-28** can change it without a code change. It is validated against the
  chain rather than assumed.

## Remaining open questions

Touched, not resolved: **OQ-01**, **OQ-03** (empty mappings warn), **OQ-04** (empty equipment
pool warns), **OQ-27** (identity enforced), **OQ-28** (stored, defaulted per A-07),
**OQ-33** (Beam deactivation with live paths — no paths exist yet).

## The gate

**S9 cannot start until OQ-25, OQ-26 and OQ-27 are answered.** This was flagged in the design
pass and every slice report since; S8 is the last slice that could proceed without them.

- **OQ-25** — is frequency reuse permitted between two Beams sharing the same Gateway or Hub
  uplink Frequency Window? This determines the **key of the central spectrum-overlap
  exclusion constraint**. Removing `Interference Domain` (§4) made `Beam` the reuse key
  (**A-01**), and two Beams fed from the same gateway antenna and the same hub-uplink window
  would be allowed to overlap by a Beam-keyed constraint — which may be physically wrong.
  This is the single largest correctness risk in the model.
- **OQ-26** — is remote-terminal equipment in scope? A second equipment reference on
  `BeamDirectionConfig` is additive; deciding after S9 is not.
- **OQ-27** — may a Beam use a sub-range of its window? S8 enforces identity. Changing that
  alters containment validation and the gap engine together.

Answering them after S9 means migrating a constraint-bearing table with live data.

## Next slice

**S9 — Reservations, exclusion constraints and the gap engine**, gated on the three questions
above. It is where the platform stops describing spectrum and starts *guaranteeing* that two
allocations cannot occupy the same Hz — the `int8range` exclusion constraint of §8.3, the
`reserves_spectrum` boolean of **A-12**, and the gap detection that makes free capacity
answerable.
