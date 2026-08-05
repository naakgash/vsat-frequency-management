# Slice S12 — Lifecycle, approvals and revisions

**Phase:** 6
**Report format:** Root Specification §27

---

## Goal

The §15.2 transition graph, second-person approval, the `ON_AIR` revision that closes the old
period before opening the new one, and optimistic locking with the field-level difference view
§15.5 asks for.

Until now an allocation was created in a status and stayed there. This is the slice that makes
the status mean something.

## Files created or changed

**satnet_paths** — `lifecycle.py` (new: the graph, transitions, editing, revision),
`services.py` (`create_revision`, `rewrite`, `occupancies_of`, `_derived_fields`),
`constants.py`, `models.py` (eight permissions), `views.py`, `urls.py`,
`migrations/0004_lifecycle_capabilities`

**approvals** (new module) — `models.py`, `services.py`, `views.py`, `urls.py`, `constants.py`,
`apps.py`, `migrations/0001` and `0002`

**spectrum** — `services.set_status`, and the conflict translation extended to updates

**accounts** — eight capabilities in the matrix, `migrations/0012`

**config** — `SUSPENDED_RETAINS_SPECTRUM` and `REQUIRE_SEPARATE_APPROVER`, `INSTALLED_APPS`,
the approvals URL mount

**Interface** — `templates/satnet_paths/{_lifecycle,stale,refused,edit,revise}.html`,
`templates/approvals/queue.html`, the detail screen's action bar, revision history and decision
list, navigation

**Tests** — `tests/satnet_paths/{test_lifecycle,test_editing,test_revisions}.py` (56),
`tests/approvals/test_approvals.py` (18), the shared lifecycle fixtures in `tests/conftest.py`

**Documentation** — ADR-0014, this report

## Database impact

| Table | Notes |
|---|---|
| `approval_decision` | Who decided, when, what they said, and the transition it caused. Append-only. |
| `satnet_path` | Eight new permissions. No new column: `record_version`, `revision_group`, `revision_number` and `supersedes` have been there since S11. |

**`ck_approval_decision_transition`** pins each outcome to the move it may cause: approved goes
`PENDING_APPROVAL → ON_AIR`, rejected goes `PENDING_APPROVAL → PLANNED`, and nothing else is a
decision. A row claiming to have approved something into `SUSPENDED` would make the trail
disagree with §15.2, and the trail is what an audit reads.

**`trg_approval_decision_immutable`** makes the table append-only in the database, like
`audit_event` and for the same reason (**A-15**): "there is no edit screen" is not a guarantee
while `queryset.update()`, a maintenance script and a psql session all exist.

## The three things this slice is really about

**A status change is a spectrum change.** Planning a draft *writes* its occupancy rows and can
be refused by the exclusion constraint; retiring releases them; suspending moves them or
releases them depending on a setting. The reservation table is not a mirror of the status
column that something reconciles later — it moves in the same transaction, or the transition
does not happen.

That makes one failure possible that did not exist before: **resuming a suspension whose
spectrum was released and then taken**. It is the constraint working, and it arrives as a
sentence about somebody else's transmission rather than as an integrity error, because
`spectrum.services` now translates the conflict on updates as well as on inserts.

**The order inside `revise` is the design, not an implementation detail.** The predecessor's
period closes and its spectrum is released *before* the successor is written, because the
exclusion constraint is `IMMEDIATE` (**A-14**) and most revisions keep their own frequency.
Reverse the two statements and a revision is refused by the row it is replacing — an error that
looks like a spectrum conflict and is actually a bug in the write order.
`test_a_revision_keeping_the_same_frequency_is_accepted` is that case, deliberately.

**§12's separation of duties is enforced, including against administrators.**
`docs/design/03` §2.1 marks the approve, reject, suspend and retire rows "—" for admin, and this
slice takes that literally: an administrator who must approve something is given the Approver
role — a grant somebody can see and revoke — rather than inheriting the authority with the job
title. Three tests hold it: an Operator cannot suspend, an Approver cannot plan, and an
administrator cannot retire.

## Two open questions, implemented as positions

| Question | Setting | Default | Why that default |
|---|---|---|---|
| **OQ-08** — does a suspension hold its spectrum? | `SUSPENDED_RETAINS_SPECTRUM` | retain | §15.3 recommends it, and it is the safer error: releasing means a suspension can silently become unresumable (ADR-0017). |
| **OQ-11** — must the approver be a second person? | `REQUIRE_SEPARATE_APPROVER` | on | §12's Approver role is decorative without it. |

Both are tested **both ways**, which is the whole reason `reserves_spectrum` is a stored column
rather than a predicate on `status` (**A-12**).

**Deviation, stated:** `docs/design/02` §8 sketches a `SystemSetting` table read through
`operations.settings.get()`. These are Django settings instead, because the readers sit *below*
`operations` in the module layering and a database-backed store there would be unreachable from
the code that needs it without inverting the dependency. A settings screen can back these two
names with a table later; nothing above them changes.

## Security and permission impact

- Eight new capabilities, one per transition (`docs/design/03` §2.2). Splitting them is the
  point: an Operator plans, submits and revises; an Approver decides, suspends and retires;
  cancelling — which only ever applies to something not yet on air — is shared.
- **Object scope still applies** (**A-17**) to every transition, delegated to
  `satnets.scope.may_act_on` so the Satnet, the Path and the lifecycle cannot disagree about
  who may act.
- **An approval cannot be reached through the plain transition service.** `lifecycle.transition`
  refuses `approve` and `reject` unless `approvals.services` is calling, because a decision that
  skipped it would move an allocation on air and leave no `ApprovalDecision` behind (§18).
- A refused decision — self-approval, wrong status, spectrum taken — **records nothing**. A
  decision row for a move that did not happen says the allocation went on air when it did not.
- The approval queue is readable by every role that can read an allocation. Hiding it from the
  operator who submitted turns "where is my allocation" into a question for somebody else.

## §15.5 — the stale submission

`record_version` rides on every lifecycle form and every edit form, and the service compares it
before doing anything. On a mismatch the operator gets the **fields that differ**, not a shrug:
"this record changed, reload" makes somebody redo work they may not need to redo, and when the
other edit touched a different field entirely it makes them redo it for nothing.

One thing that had to be got right for the diff to exist at all: the comparison reads a **fresh
row from the database**, not the instance in hand. A `ModelForm` bound with `instance=path`
writes the submitted values onto that instance while it validates, so comparing against it would
compare the submission with itself and report no differences — a diff screen that is always
empty, on the one screen whose entire job is to show what moved. The HTTP test is what caught
it.

## Tests added

1036 total, up from 962. 74 new.

| File | Covers |
|---|---|
| `test_lifecycle.py` (26) | Every status's legal moves and an illegal one; the refusal naming what *is* possible; planning writing occupancy rows; submitting moving them rather than rewriting; retiring releasing; freed spectrum being takeable; **the suspension policy both ways**, including the resume that collides; Operator/Approver/**Admin** boundaries; scope; the offered-move list; a stale button; the approval gate |
| `test_editing.py` (17) | Editable statuses and the three that are not; the release-then-reserve rewrite; a draft edit writing nothing; the stale refusal, its field-level diff and its no-op; the edit screen; a stale POST returning 409 with the diff; buttons carrying the version; an illegal move over HTTP; the detail screen offering only what the reader may do |
| `test_revisions.py` (13) | A revision keeping its own frequency; the predecessor closed, retired and released; half-open handover; the chain's group and order; the list showing only the successor; **approval not inherited**; recomputation rather than copying; refusals for a superseded, cancelled or backdated revision; an Approver not revising; a refused revision leaving the original untouched |
| `test_approvals.py` (18) | Approval and rejection with what each does to the spectrum; a rejection surviving a later approval; **self-approval refused**, and permitted when the setting is off; a refusal recording nothing; Operator refused; wrong status refused; no third outcome; the decision immutable and undeletable; the CHECK against a decision that disagrees with the graph; the queue; three HTTP paths |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.14 — approver path | **Met.** A second person decides, the decision is recorded and immutable, and the approver cannot bypass the overlap constraint — approving reserves through the same service everything else does. |
| §26.17 — traceability | **Advanced.** Every transition, decision, refusal and stale submission is an audit event, and the decision is a record in its own right. |
| §26.16 — derived values are engine-owned | **Held.** Editing and revising both write through `_derived_fields`, the same dictionary creation uses, so the three writers cannot drift. |
| §26.20 — no invented RF value | **Held.** Nothing is seeded. |

## Verification performed

```
pytest                                   1036 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (12 modules, calculations strict)   no issues in 141 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
```

## Remaining open questions

**OQ-08** and **OQ-11** — implemented as settings with the recommended defaults, and still open:
what the platform holds is a position, not an answer.

**OQ-12** — temporary or hourly future allocations. The time model is `timestamptz` throughout
and the lifecycle imposes no granularity, so nothing blocks them; what is missing is a
requirement, not a capability.

**OQ-22** — still the one gap that cannot be closed by building.

## What is deliberately not built

**No `SystemSetting` table or settings screen.** See the deviation above.

**No bulk transition.** Approving forty allocations in one click is a plausible request and a
poor idea while each one can be refused for a different reason; the queue links to each record.

## Next slice

**S13 — Satnet Path table, filters, saved views and dashboard.** The §10.3 table with grouped
columns and specification popovers, saved views, and the dashboard cards fed by the selectors
S9 already built.
