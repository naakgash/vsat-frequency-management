# Slice S13 — The Satnet Path table, saved views and the dashboard

**Phase:** 6
**Report format:** Root Specification §27

---

## Goal

§10.3's table — grouped columns, filters, sorting, specification popovers and saved views — and
the dashboard the landing page has been standing in for since S1.

## Files created or changed

**reporting** (new module) — `columns.py`, `filters.py`, `selectors.py`, `services.py`,
`models.py`, `views.py`, `urls.py`, `constants.py`, `apps.py`,
`templatetags/table_tags.py`, `migrations/0001`

**inventory** — `templatetags/utc_tags.py` moved here from `operations` (see below)

**satnet_paths** — the S11 placeholder list view, its route and its template are gone; the
table replaces them

**accounts** — one capability, `migrations/0013`

**config** — `reporting` in `INSTALLED_APPS`, the dashboard at `/`, the table mounted before the
Satnet Path routes

**Interface** — `templates/reporting/{satnet_path_table,dashboard}.html`, navigation

**Tests** — `tests/reporting/{test_table,test_saved_views_and_dashboard}.py` (44)

**Documentation** — this report

## Database impact

| Table | Notes |
|---|---|
| `saved_view` | Owner, page, filters, columns, sort, shared flag. `uq_saved_view_owner_page_name`. |

No index was added to `satnet_path`. `docs/design/04` §6 names several for list filtering, and
they are worth adding when there is data to measure — **OQ-15** puts the ceiling at 10⁵ rows,
and an index chosen against an empty table is a guess with a maintenance cost.

## The three things this slice is really about

**The headings are not in the template.** §2 forbids the same description living in two places,
and a table is the worst offender: twenty columns, each with a name, a unit and an explanation
an administrator may edit. So a column *names a specification code* and the heading comes from
the dictionary through the existing popover component. The columns that are not specification
values — a link, a status badge, a Hub code — carry their own label, and the dataclass refuses a
column that has both or neither. That refusal is the guard rail: it makes "where does this
heading come from" a question with exactly one answer per column.

**The URL is the state.** Filters, chosen columns and the sort all live in the query string.
Applying a saved view is therefore a *redirect*, not a second code path — so a saved view and a
copied link cannot disagree about what they show, and two people opening the same URL see the
same table. Holding any of it in the session would break both properties quietly.

**Every filter is declared, and nothing else reaches the ORM.** The parameters come from a URL.
A filter layer that passed unknown keys to `filter(**params)` would let a visitor query columns
no screen offers — including ones scope exists to hide. `FILTERS` is the whole of what may be
asked, and `test_an_undeclared_parameter_never_reaches_the_orm` says so directly.

## What sharing a saved view does and does not share

A shared view shares the **question**, never the answer. The table it produces is scope-filtered
on every read like any other listing, so an administrator's "everything" view shows each reader
only their own spectrum — and an anonymous caller nothing at all. That is what makes
`is_shared` safe to have, and it is asserted rather than assumed.

Deleting is the owner's alone, **including against an administrator**. A saved view is a
personal working tool rather than operational data; §20's "no hard deletes" is about the record
of what was allocated, which this is not.

## The N+1 guard

A Satnet Path row can reach its Satnet, Hub, Beam, Gateway and Decimator. Rendering those one
row at a time is the difference between a page and an outage, and no single-row test notices.

`ROW_RELATIONS` states the joins **independently of the chosen columns** — selecting joins per
column would make the query plan depend on a checkbox — and the test counts queries for one row
and then for three, with every column selected. Comparing counts rather than pinning one is
deliberate: a fixed number turns every unrelated middleware change into a failing test, and what
is worth protecting is the slope, not the intercept.

## What this slice moved

**`utc_tags` now lives in `inventory`, beside `rf`.** S11a put it in `operations`, which was
fine while only templates loaded it — templates load a tag library by name, not by import. The
table's cell renderer *imports* it, and `operations` is the top of the module graph, so nothing
may import from it. `inventory` is the lowest module that owns templates, which makes it the
one place a display filter can live and stay reachable from everywhere above. The layering
contract caught this, which is what it is for.

**The S11 Satnet Path list is gone.** It was a placeholder for exactly this table, and keeping
both would leave two answers to "where do I see my allocations". Everything that linked to it —
navigation, three breadcrumbs, two tests — now points at the table.

**The landing page is the dashboard, and it requires a session.** It shows scoped figures, so
it stopped being a public page; the `home` entry has left the URL-coverage allowlist.

## Security and permission impact

- One new capability, `reporting.add_savedview`, held by **every** role. Saving a table setup is
  a personal working tool, and an Observer — whose job is reading tables — needs it most.
- Every listing goes through `satnet_paths.selectors.current`, so a filter can only ever reduce
  what somebody sees. Reading a Path stays open to any authenticated user, which is S11's
  decision and is now pinned by a test in this module too.
- Deleting somebody else's saved view is a 403, not a message: the interface cannot produce that
  request, so it arrives only from a hand-made one.

## Tests added

1082 total, up from 1036. 44 new (the collected count moves by a few because
`tests/test_imports.py` sweeps application modules).

| File | Covers |
|---|---|
| `test_table.py` (21) | Each filter, including **half-open `in force at`** and the holds-spectrum question; an unparseable filter ignored; an undeclared parameter never reaching the ORM; sorting both ways and the unknown-sort fallback; unsortable columns; the default column set; an unknown column in a saved view dropped; the heading-source rule; read scope; current revisions only; the page for every role; the filter summary; **the query count against row count** |
| `test_saved_views_and_dashboard.py` (23) | Saving, re-cleaning what it stores, replacing by name, two people sharing a name; private versus shared; **a shared view sharing the question and not the answer**; the query-string round trip; owner-only deletion including against an administrator; four HTTP paths; dashboard counts matching the table; every status present at zero; the reserving total; **utilisation coming from the gap engine**; the bound on utilisation work; the dashboard requiring a session; whole-number percentages |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.11 — spectrum view and free capacity | **Met.** The dashboard reports free capacity per Beam direction straight from the gap engine, and the table is the §10.3 listing. |
| §26.2, §26.3 — specification dictionary in use | **Advanced.** Table headings are dictionary entries with their popovers, which is the first place §2's rule is load-bearing at scale. |
| §26.20 — no invented RF value | **Held.** Nothing is seeded. |

## Verification performed

```
pytest                                   1082 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (13 modules, calculations strict)   no issues in 152 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          up to date
```

## Remaining open questions

**OQ-15** — expected volumes. The list indexes `docs/design/04` §6 names are not built: an index
chosen against an empty table is a guess with a maintenance cost, and the filters are written so
that adding one later changes nothing above the ORM.

**OQ-22** — still the one gap that cannot be closed by building.

## What is deliberately not built

**No column reordering or width persistence.** A saved view stores *which* columns, not how they
are arranged; the registry's order is the table's order. §10.3 asks for grouped columns and
column selection, and both are here.

**No CSV or Excel from the table.** Export is S14, with the formula-injection protection §21.12
requires — a "download this table" button added here would be the same feature without that.

## Next slice

**S14 — Export.** The normalized export first, with formula-injection protection on every
written cell and scope filtering at the queryset; the legacy-style export stays sized against a
real sample workbook (**OQ-18**).
