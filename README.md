# VSAT Spectrum Allocation Platform

VSAT spectrum planning, frequency allocation and operational tracking platform.

Replaces spreadsheet-based frequency planning with a controlled multi-user application built around
the hierarchy `Satellite → Beam → Satnet → Satnet Path`, with derived engineering values, guided
operator workflows, and overlap prevention enforced at the UI, service and PostgreSQL layers.

**Status:** Design phase. No application code yet — the design documents below are under review and
implementation has not started.

**Source of truth:** `VSAT Spectrum Allocation Platform — Root Specification v1.0`.

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
