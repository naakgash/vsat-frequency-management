# Slice S16 — Audit UI

**Phase:** 7
**Report format:** Root Specification §27

---

## Goal

The trail S2 started writing and every slice since has written into, given a screen: search by
actor, action, object, outcome and period; one event with the field-level before/after
difference §18 asks for; and the whole history of one record, oldest first.

## Files created or changed

**audit** — `selectors.py`, `views.py`, `urls.py` (all new), `models.py` (two partial indexes),
`migrations/0003`; `templatetags/utc_tags.py` **moved here** from `inventory`

**reporting** — one import updated to follow the moved filter

**config** — the `/audit/` mount

**Interface** — `templates/audit/{search,event,history,_events,_changes}.html`; the Audit entry
in the navigation becomes a link; the Satnet Path detail page links to its own history

**Tests** — `tests/audit/test_audit_ui.py` (52)

**Documentation** — this report

## Database impact

Two indexes, no table and no column. The GIN indexes on the JSONB payloads that the plan lists
under "Database" were already created by S2's initial migration, so the field-level difference
search has been indexed since the trail existed.

| Index | For |
|---|---|
| `audit_import_batch_idx` | "What did that import actually change" — **partial**, on rows that have a batch |
| `audit_request_idx` | "What did that one save do" — **partial**, on rows that have a request |

Both partial, and the reason is the same for each: almost every row in this table has a null in
those columns, and an index over the nulls would be mostly dead weight on the busiest write path
in the product. The import batch column has existed since S2 (`audit` may not import
`imports_exports`, so it was always a plain UUID); S15 is what first gave it values, which is
what makes indexing it worth doing now rather than then.

## The three things this slice is really about

**Visibility is a queryset, not a check after the fetch.** `docs/design/03` §2.1 gives an
administrator every event and an Operator or Approver *their own actions*; an Observer holds
neither capability and sees nothing. That is expressed once, in `selectors.visible`, and every
other function here starts from it — so a filter can only ever *reduce* what somebody sees, and
a search link pasted into an incident channel shows the recipient their own trail rather than
the sender's.

An event outside somebody's visibility is a **404, not a 403**. "This event exists" is itself
something an audit trail should not disclose, and a 403 says it.

**There is no object scope here, and that is not an omission.** An Operator can only have
authored events about objects they held grants for, so "own actions" already carries **A-17**'s
conjunctive Beam-and-Hub rule with it. A scope filter on top would be a check that can never
fail, which reads like protection and provides none.

**There is no write route.** Not a protected one — none at all. No form, no POST handler, no
delete view, no admin registration. §18 and **A-15** make an event immutable and a database
trigger enforces it against direct SQL, but the absence of a route is the first line and the
only one a reader can check by looking. A test enumerates `audit.urls` and fails on any view
implementing an unsafe method.

## The layering problem this slice had to solve

`audit` is the **bottom** of the dependency graph (`docs/design/01` §1) because every module
records into it, and `lint-imports` holds a contract saying it may import no other local module.
Putting screens inside it collides with that twice.

**Authorisation.** Every other module protects a view with
`accounts.mixins.AuditedPermissionRequiredMixin`, which records the denial through
`accounts.policy`. Importing that here would invert the graph in one line. So `audit.views`
carries its own mixin doing the same job with the recorder that already lives in this module —
`audit.services.record`, which is what `accounts.policy` calls anyway. Same 403, same
`PERMISSION_DENIED` event, same judgement that an anonymous redirect to the sign-in page is not
a refusal worth recording.

**Templates.** `{% load utc_tags %}` is resolved by name, so `lint-imports` would never see it,
which makes it the more dangerous of the two. The filter lived in `inventory` because S13 put it
in the lowest module that owned templates at the time. `audit` now owns templates and is lower,
so the filter **moved down again** — one definition still, which is the whole point (§2,
ADR-0011). A copy in `audit` would be a second answer to "how does this platform print a time",
and the two would part company the first time one was improved.

The search form's control values are resolved in `selectors.form_fields` rather than looked up
in the template for the same reason: a dictionary lookup in a Django template needs a custom
filter, the one that exists lives in `reporting.templatetags`, and handing the template a list
it can iterate needs no filter at all.

A test asserts the property structurally — it parses every module under `audit/` and fails on an
import of any local package — because a convenient import is exactly the kind of thing that
undoes this without anybody noticing.

## What can be searched, and what deliberately cannot

Nine declared filters: actor, action, outcome, object type, object, from, to, request, import
batch. Every one is declared, for the reason `reporting.filters` gives — the parameters come
from a URL, and a layer that passed unknown keys to `filter(**params)` would let a visitor query
columns no screen offers. A value that will not parse is dropped rather than fatal, because a
hand-edited URL is routine and a 500 on one is not.

**Two filters are new because S15 made them askable.** *Import batch* answers "what did that file
actually change", which is the first question anybody asks after a migration. *Request* answers
"what did that one save do" — a single save writes several events, a Satnet Path created, its
reservations placed, its approval recorded, and without the request id reading a busy trail means
correlating by timestamp and hoping.

**There is no free-text search.** `message` and `object_repr` are the tempting columns and both
would be an unindexed scan on the largest table in the product. §20 asks for searchable *code*
fields, which is what the Satnet Path table provides; a trail is searched by who, what and when.

**The action list comes from the data, not from a registry.** `audit.constants` says each module
declares its own codes, so a form offering one nothing has ever emitted teaches people the
filter is broken.

**The `to` filter is inclusive, unlike a validity period.** **A-10**'s half-open rule is about
periods that abut — one allocation ending exactly where the next begins. A search box is not
that: somebody typing an end time means "up to then", and excluding an event that landed on the
second would look like the trail had lost it.

## Reading a history

Oldest first, unlike every other listing in the product. A history is read as an account of what
happened, and one that starts with the ending is a list of events rather than a story.

Reached by `app_label.ModelName` and identifier rather than by a route per entity: the trail
holds rows about twenty kinds of object, and a view each would be twenty places to forget the
visibility rule. The screen never joins to the object it describes — audit rows outlive what
they describe (§20), and a history that 404s once a record is gone is a history of nothing. The
name shown comes from the most recent `object_repr` the events themselves carry.

The Satnet Path detail page links to its own history rather than embedding it. The revision
chain already there says what the allocation *was*; the trail says who did what to it and when.
Two questions, two screens, one place each to keep right.

## Security and permission impact

- **No new capability.** `view_auditevent` and `view_all_auditevent` were seeded in S2 and this
  slice is the first thing to use them. The matrix is unchanged.
- **Every denial is recorded**, by the mixin described above, so a view-level refusal reaches
  the trail exactly as a service-level one does (§18).
- **An Observer gets no navigation entry and a 403 on the URL.** Hiding the link is presentation;
  the view enforces it, and a test signs in as an Observer and asks for the page.
- **Sensitive values were redacted on the way in**, not on the way out (`audit.services._sanitize`,
  S2). A password hash in this table would be a credential in a record that can never be
  corrected or deleted, so it never reaches it — which is why the event screen can safely offer
  the full recorded state behind a disclosure.

## Tests added

1242 total, up from 1194. 52 new, less one relocated import.

| Area | Covers |
|---|---|
| Visibility (6) | An administrator sees everything; an Operator and an Approver see their own; an Observer and an anonymous reader see nothing; **a filter can only narrow what is visible** |
| Search (11) | By actor (and case-insensitively), action, object, outcome, period, **import batch**, **request** — the last driven through a real HTTP request, because the id is stamped by middleware; an inclusive `to` boundary; an undeclared parameter never reaching the ORM; an unparseable filter dropped; the action list read from the data and narrowed like everything else |
| Differences (3) | Only changed fields appear; the same diff for four shapes of change, so every entity reads alike; a creation showing every field as new |
| History (3) | Oldest first; narrowed to the reader; empty rather than broken for an object with no events |
| The screens (10) | Administrator search; an Operator seeing only their own; an Observer refused **and the denial recorded**; an anonymous reader redirected **without** being audited; an invisible event 404 rather than 403; the difference rendered; the history page; the screen saying whose trail it is; the Satnet Path link; the navigation entry |
| Pagination (2) | A page bound, and a pager link that keeps the search |
| What must not exist (3) | **No route accepts an unsafe method**; **no module under `audit/` imports a local package**; the trail still refuses UPDATE and DELETE at the database |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.17 — traceability | **Met.** The trail is written by every slice and is now searchable by actor, action, object, period, request and import batch, with the field-level difference on every event. |
| §26.19 — import and export | **Held.** The import batch filter is what makes a committed batch reviewable after the fact. |
| §26.16 — permissions enforced in the backend | **Held.** Visibility is the queryset; the view enforces the capability; a denial is recorded. |
| §26.20 — no invented RF value | **Held.** |

## Verification performed

```
pytest                                   1242 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (14 modules, calculations strict)   no issues in 177 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          unchanged
```

## Deviations from the plan

**The GIN indexes already existed.** The plan lists them under "Database"; S2's initial migration
created them. What this slice adds instead are the two partial indexes the new filters need.

**`utc_tags` moved from `inventory` to `audit`.** Not in the plan, and unavoidable: an audit
template rendering a timestamp had to either reach up to `inventory` or hold a second copy of the
rule. The move keeps one definition and puts it in the lowest module that owns templates, which
is the same reasoning S13 used one level higher.

**No audit export.** §17.2's export is over the Satnet Path table; exporting the trail is a
different thing with a different shape, nothing in §17 asks for it, and the search URL is
already the shareable artefact. Stated rather than silently skipped.

## Remaining open questions

**OQ-15** — expected volumes, and this is the slice where it starts to matter. The trail is the
largest table in the product and grows monotonically (§20: never hard-deleted). The screen is
paginated, so one request's work is bounded — but Django's paginator issues a `COUNT(*)`, which
is the part that gets slow first. Above roughly 10⁶ rows the fix is keyset pagination and
dropping the exact total for an approximate one; both are worth doing only once somebody says
what the volumes are. Stated rather than pre-solved, as in S14.

**OQ-22** — still the one gap that cannot be closed by building.

## Next slice

**S17 — Production hardening, backup and restore.** Phase 8: the operational half of §21 and
§22 — backup and verified restore, log handling, and the deployment checklist.
