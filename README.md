# VSAT Spectrum Allocation Platform

VSAT spectrum planning, frequency allocation and operational tracking platform.

Replaces spreadsheet-based frequency planning with a controlled multi-user application built around
the hierarchy `Satellite → Beam → Satnet → Satnet Path`, with derived engineering values, guided
operator workflows, and overlap prevention enforced at the UI, service and PostgreSQL layers.

**Status:** Slices **S1–S4** delivered. Application foundation, PostgreSQL 16 with the required
extensions, health endpoints, the English interface shell, CI guard rails, authentication with four
roles behind a single backend authorization choke point, an append-only audit trail, the
admin-managed Specification Dictionary with its accessible information popover, and the five
independent inventory entities with object-level scope. Dependent inventory begins at S5; see the
[slice plan](docs/design/05-vertical-slice-plan.md) for what lands when.

The inventory ships **empty**: every value it would hold is an unresolved RF engineering question,
and a plausible-looking made-up satellite would be indistinguishable from real data once loaded.

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

## Architecture decisions

- [ADR-0001 — Modular monolith](docs/adr/0001-modular-monolith.md)
- [ADR-0002 — Server-rendered Django with HTMX](docs/adr/0002-django-htmx-server-rendered.md)
- [ADR-0003 — Integer Hz and Decimal roll-off](docs/adr/0003-integer-hz-and-decimal-rolloff.md)
- [ADR-0011 — Specification Dictionary as the single source of field wording](docs/adr/0011-specification-dictionary.md)
- [ADR-0013 — Append-only audit, enforced by the database](docs/adr/0013-append-only-audit.md)

## Before implementation starts

Three questions can change the database schema and should be answered before slice **S9**
(reservations and exclusion constraints):

- **OQ-25** — is frequency reuse permitted between two Beams sharing the same Gateway/Hub uplink
  Frequency Window?
- **OQ-26** — is remote-terminal equipment (remote BUC/LNB) and its L-band IF in scope?
- **OQ-27** — may a Beam use a sub-range of its Payload Path's Frequency Window?

The remaining RF engineering values (Frequency Windows, translations, polarization mappings,
equipment limits, guard policies, golden examples) are required before production activation, not
before implementation: each is a row in a table, not a branch in the code. No value is invented.

## Planned stack

Python 3.12 · Django 5.2 LTS · PostgreSQL 16 (`btree_gist`) · HTMX 2 · Bootstrap 5 · ECharts ·
openpyxl · Gunicorn · Nginx · Docker Compose · pytest · Hypothesis · Ruff · mypy
