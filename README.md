# VSAT Spectrum Allocation Platform

VSAT spectrum planning, frequency allocation and operational tracking platform.

Replaces spreadsheet-based frequency planning with a controlled multi-user application built around
the hierarchy `Satellite → Beam → Satnet → Satnet Path`, with derived engineering values, guided
operator workflows, and overlap prevention enforced at the UI, service and PostgreSQL layers.

**Status:** Slices **S1–S15** delivered, plus the lettered schema slices S0, S9a, S10a and S11a.
The application foundation, PostgreSQL 16 with the required extensions, health endpoints, the
English interface shell and CI guard rails; authentication with four roles behind a single backend
authorization choke point and an append-only audit trail; the admin-managed Specification
Dictionary; independent and dependent inventory as effective-dated master data that is superseded
rather than overwritten; the pure calculation engine — bandwidth, edges, guards, payload
translation, RF/IF conversion and equipment matching; the Beam Builder and its spectrum
assignments; **reservations and the PostgreSQL exclusion constraint that is the last line of
defence against an overlap**; Satnets, the guided Satnet Path wizard, the §15.2 lifecycle with
approvals and on-air revisions; the table, saved views and dashboard; and both halves of §17 in the
platform's own shape — the normalized export and the two-stage import. See the
[slice plan](docs/design/05-vertical-slice-plan.md) for what remains.

The inventory ships **empty** — no satellite, band, window, translation, polarization mapping or
guard value. Every value it would hold is an unresolved RF engineering question, and a
plausible-looking made-up one would be indistinguishable from real data once loaded.

**Source of truth:** `VSAT Spectrum Allocation Platform — Root Specification v1.0`.

---

## Quick start

Requires Docker and Docker Compose.

```bash
cp .env.example .env          # then set DJANGO_SECRET_KEY
make up                       # database, migrations, health check — one command (spec 22.1)
```

The application is served at http://127.0.0.1:8000.

For native development against a local PostgreSQL 16 cluster:

```bash
make install                  # uv sync from the committed lock file
make migrate
make run
```

### Everyday commands

| Command | Purpose |
|---|---|
| `make test` | Test suite against real PostgreSQL |
| `make check` | Ruff, mypy and the module dependency contract |
| `make test-db` | Database constraint tests only |
| `make vendor` | Re-fetch vendored front-end assets |
| `manage.py seed_demo` | Create the four demo accounts (local development only) |
| `manage.py check_specifications` | Report dictionary entries still awaiting engineering input |
| `make help` | All targets |

PostgreSQL is required — there is no SQLite path. Exclusion constraints, `int8range` and
`btree_gist` are the final defence against overlapping allocations (spec §8.3), and none of them
exist in SQLite, so a green SQLite suite would prove nothing about the behaviour that matters most.

---

## Design documents

| # | Document | Contents |
|---|---|---|
| 00 | [Assumptions and OPEN QUESTIONs](docs/design/00-assumptions-and-open-questions.md) | 20 modelling assumptions with blast radius, and the 35-item `OPEN QUESTION` register |
| 01 | [Repository structure](docs/design/01-repository-structure.md) | Module layout, dependency direction, toolchain, CI guard rails |
| 02 | [Entity relationship design](docs/design/02-entity-relationship-design.md) | Entities, relationships, and the entities deliberately absent |
| 03 | [Permissions and authorization](docs/design/03-permissions-and-authorization.md) | Capability matrix, scope model, four enforcement layers, URL map |
| 04 | [Database constraints and transactions](docs/design/04-database-constraints-and-transactions.md) | Exclusion constraints, CHECKs, indexes, triggers, transaction boundaries |
| 05 | [Vertical slice plan](docs/design/05-vertical-slice-plan.md) | 19 slices from foundation to cutover, with the §27 report format |

## Delivered slices

| Slice | Report | Acceptance criteria |
|---|---|---|
| S1 — Foundation and Health | [docs/slices/01-foundation.md](docs/slices/01-foundation.md) | §26.1, §26.5, §26.9 enforced by CI; §26.18 partial |
| S2 — Identity, Roles and Audit | [docs/slices/02-identity-roles-audit.md](docs/slices/02-identity-roles-audit.md) | §26.16 mechanism; §26.17, §26.18 partial |
| S3 — Specification Dictionary | [docs/slices/03-specification-dictionary.md](docs/slices/03-specification-dictionary.md) | §26.2, §26.3 met; §26.20 enforced by test |
| S4 — Independent Inventory | [docs/slices/04-independent-inventory.md](docs/slices/04-independent-inventory.md) | §26.4 met; §26.20 enforced by test; settles OQ-30 |
| S5 — Dependent Inventory and Versioning | [docs/slices/05-dependent-inventory.md](docs/slices/05-dependent-inventory.md) | §26.4 extended; §26.15, §26.17 advanced; §26.20 enforced by test |
| S6 — Calculation Engine | [docs/slices/06-calculation-engine.md](docs/slices/06-calculation-engine.md) | §26.10 partial; §26.16 met for bandwidth, edges and guards |
| S7 — Translation, IF and Equipment | [docs/slices/07-translation-conversion-matching.md](docs/slices/07-translation-conversion-matching.md) | §26.10, §26.12 met for the calculation half; §26.20 gated on OQ-22 |
| S8 — Beam and Beam Builder | [docs/slices/08-beam-builder.md](docs/slices/08-beam-builder.md) | §26.6, §26.7 met; §26.20 enforced by test |
| S0 — RF Confirmation Package | [docs/slices/00-rf-confirmation-package.md](docs/slices/00-rf-confirmation-package.md) | §26.20 given a way to be answered; sheets generated from the models |
| S9a — Spectrum Resources and Assignments | [docs/slices/09a-spectrum-resources-and-assignments.md](docs/slices/09a-spectrum-resources-and-assignments.md) | Answers OQ-25, OQ-26, OQ-27; supersedes **A-01** |
| S9 — Reservations and Gaps | [docs/slices/09-reservations-and-gaps.md](docs/slices/09-reservations-and-gaps.md) | §26.8, §26.11 met; the exclusion constraint lands |
| S10 — Satnets | [docs/slices/10-satnets.md](docs/slices/10-satnets.md) | §26.14 met; conjunctive scope (**A-17**) reaches an operational record |
| S10a — Validity Containment | [docs/slices/10a-validity-containment.md](docs/slices/10a-validity-containment.md) | Answers OQ-32; the Beam gains a validity period |
| S11 — Guided Satnet Path creation | [docs/slices/11-satnet-path-wizard.md](docs/slices/11-satnet-path-wizard.md) | §26.9, §26.10, §26.11, §26.13, §26.16 met; §26.12 partial |
| S11a — Controlled hardware references and UTC | [docs/slices/11a-controlled-hardware-references-and-utc.md](docs/slices/11a-controlled-hardware-references-and-utc.md) | Answers OQ-09, OQ-10, OQ-23; widens the OQ-22 harness; §26.20 held |
| S12 — Lifecycle, approvals and revisions | [docs/slices/12-lifecycle-approvals-revisions.md](docs/slices/12-lifecycle-approvals-revisions.md) | §26.14 met; §26.17 advanced; OQ-08 and OQ-11 implemented as settings |
| S13 — Table, saved views and dashboard | [docs/slices/13-table-saved-views-dashboard.md](docs/slices/13-table-saved-views-dashboard.md) | §26.11 met; §26.2/§26.3 advanced; table headings come from the Specification Dictionary |
| S14 — Export | [docs/slices/14-export.md](docs/slices/14-export.md) | §26.19 met for the normalized export; §26.17 advanced; §21.12 enforced at one choke point; legacy layout gated on OQ-18 |
| S15 — Import: dry-run and commit | [docs/slices/15-import-dry-run-and-commit.md](docs/slices/15-import-dry-run-and-commit.md) | §26.19 met for the import; §26.16 held from a new angle; SHA-256 verified between the two stages; free-capacity rows ignored, never imported |

Slices are listed in the order they were delivered, which is not always numerical: S0 was
written once there was a schema to generate intake sheets from, and the lettered slices are
schema changes that arrived with an answer.

## Architecture decisions

- [ADR-0001 — Modular monolith](docs/adr/0001-modular-monolith.md)
- [ADR-0002 — Server-rendered Django with HTMX](docs/adr/0002-django-htmx-server-rendered.md)
- [ADR-0003 — Integer Hz and Decimal roll-off](docs/adr/0003-integer-hz-and-decimal-rolloff.md)
- [ADR-0004 — The Beam is the root pool, and each direction is a child row](docs/adr/0004-beam-root-pool.md)
- [ADR-0006 — A Satnet Path reserves both sides, and one is the image of the other](docs/adr/0006-two-sided-reservations.md)
- [ADR-0008 — Half-open ranges, including through spectral inversion](docs/adr/0008-half-open-ranges.md)
- [ADR-0010 — One calculation engine, and it is pure](docs/adr/0010-central-calculation-engine.md)
- [ADR-0011 — Specification Dictionary as the single source of field wording](docs/adr/0011-specification-dictionary.md)
- [ADR-0012 — Master data is superseded, not overwritten](docs/adr/0012-master-data-versioning.md)
- [ADR-0005 — Satnet Path terminology](docs/adr/0005-satnet-path-terminology.md)
- [ADR-0007 — PostgreSQL exclusion constraints hold the overlap guarantee](docs/adr/0007-postgresql-exclusion-constraints.md)
- [ADR-0009 — Free capacity is calculated, never stored](docs/adr/0009-calculated-free-capacity.md)
- [ADR-0013 — Append-only audit, enforced by the database](docs/adr/0013-append-only-audit.md)
- [ADR-0014 — An on-air allocation is closed and replaced, never overwritten](docs/adr/0014-on-air-revisions.md)
- [ADR-0015 — An import is read twice and calculated once](docs/adr/0015-import-dry-run.md)
- [ADR-0016 — Guard policies resolve through a fixed hierarchy](docs/adr/0016-guard-policy-hierarchy.md)
- [ADR-0017 — What a suspended reservation holds](docs/adr/0017-suspended-reservation-policy.md)
- [ADR-0018 — Overlap is judged on a Spectrum Resource](docs/adr/0018-spectrum-resource-reuse-key.md)
- [ADR-0019 — A Beam's usable spectrum is its assignments](docs/adr/0019-beam-spectrum-assignments.md)
- [ADR-0020 — A Satnet Path lives inside the intersection of three periods](docs/adr/0020-validity-containment.md)
- [ADR-0021 — A GW ID is a reference; a Decimator is allocated through an Assignment](docs/adr/0021-hardware-references-and-decimator-assignments.md)
- [ADR-0022 — UTC is the display time zone, and it is displayed](docs/adr/0022-utc-is-the-display-time-zone.md)

## What is still open

The three questions that could change the database schema — **OQ-25**, **OQ-26** and **OQ-27** —
were answered on 2026-08-04 and are implemented (S9a, S10a). **OQ-09**, **OQ-10**, **OQ-23** and
**OQ-32** followed. The full register, with what each answer changed, is in
[docs/design/00](docs/design/00-assumptions-and-open-questions.md).

**OQ-18 now blocks both halves of §17.** The incumbent workbook is needed twice over: without a
real sample the legacy-style export cannot be written (S14) and the legacy layout cannot be read
(S15). Neither is guessed at — the normalized shape works end to end in both directions, and the
legacy pair will be sized together once a sample arrives.

**OQ-22 is the one gap that cannot be closed by building.** Section 24 asks for a worked example
from a currently operational Satnet Path, calculated independently by an RF engineer — anything
the implementation produces proves nothing about the implementation. `tests/domain/golden/` is
empty, and the build fails at Phase 9 while it stays that way.

The remaining RF engineering values (Frequency Windows, translations, polarization mappings,
equipment limits, guard policies, decimator configurations) are required before production
activation, not before implementation: each is a row in a table, not a branch in the code. No
value is invented. `docs/rf-confirmation/` is how they are asked for.

## Planned stack

Python 3.12 · Django 5.2 LTS · PostgreSQL 16 (`btree_gist`) · HTMX 2 · Bootstrap 5 · ECharts ·
openpyxl · Gunicorn · Nginx · Docker Compose · pytest · Hypothesis · Ruff · mypy
