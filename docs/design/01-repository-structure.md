# 01 — Proposed Repository Structure

**Derived from:** Root Specification §19 (Software Architecture), §19.5 (Repository Structure).
The top-level tree is fixed by §19.5 and is reproduced exactly; everything below module level is this
document's proposal.

---

## 1. Architectural shape

A **modular monolith**, server-rendered, one deployable unit, one PostgreSQL database.

Four rules keep the modules honest inside a single process:

1. **Dependency direction is one-way.** A module may import from modules *below* it in the layer
   list, never above. Enforced by an import-linter contract in CI, not by convention alone.
2. **Cross-module reads go through `selectors.py`; cross-module writes go through `services.py`.**
   No module reaches into another module's ORM models to write.
3. **`calculations/` imports nothing from Django models.** It is pure domain code operating on
   dataclasses, which is what makes it testable with Hypothesis and reusable by the importer (§11).
4. **Templates never calculate.** Any arithmetic in a template or in JavaScript is a defect; the
   backend result is authoritative (§11).

Module dependency order (lower may not import higher):

```text
config
  └── reporting, imports_exports, operations, approvals      (edge modules)
        └── satnet_paths
              └── satnets  ──  spectrum
                    └── beams
                          └── inventory  ──  specifications
                                └── calculations   (pure, imports nothing internal)
                                      └── audit, accounts   (cross-cutting, imported by all)
```

`audit` and `accounts` are cross-cutting and may be imported anywhere; they import nothing from
domain modules in return.

---

## 2. Top-level tree

```text
vsat-frequency-management/
├── config/                     # Django project: settings/, urls.py, asgi.py, wsgi.py
├── accounts/                   # User, roles, scopes, authentication, policy engine
├── inventory/                  # Satellite, Band, Gateway, Hub, EquipmentProfile,
│                               #   FrequencyWindow, PayloadPath, GuardPolicy
├── specifications/             # Specification Dictionary + info-popover component
├── beams/                      # Beam, direction configs, Beam Builder wizard
├── satnets/                    # Satnet child pool + capacity summaries
├── satnet_paths/               # Satnet Path entity, guided wizard, lifecycle
├── spectrum/                   # SpectrumReservation, gap engine, spectrum view
├── calculations/               # Pure domain: bandwidth, edges, translation, IF, Auto-place
├── approvals/                  # Approval requests and decisions
├── imports_exports/            # Excel dry-run / commit, normalized + legacy export
├── audit/                      # Append-only AuditEvent, audit UI
├── operations/                 # System settings, health endpoints, backup hooks
├── reporting/                  # Dashboard, saved views, column selection
├── templates/                  # Project-level base templates and shared partials
├── static/                     # Vendored Bootstrap 5, HTMX 2, ECharts — no CDN (§19.4)
├── tests/                      # Top-level test tree, mirrors modules (§19.5)
├── docs/
│   ├── design/                 # These design documents
│   ├── adr/                    # Architecture Decision Records (§19.6)
│   └── runbooks/               # Deploy, backup, restore, incident (§22)
├── docker/                     # Dockerfile, nginx conf, entrypoints
├── compose.yaml
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## 3. Module internal layout

Every domain module uses the same file set. Files that would be empty are omitted rather than
created as stubs.

```text
<module>/
├── __init__.py
├── apps.py
├── models.py                 # or models/ package when >~400 lines
├── constants.py              # enums / choices, importable without Django app loading
├── managers.py               # scoped querysets: .for_user(user), .reserving(), .active_at(t)
├── selectors.py              # read-side query functions (no writes, no side effects)
├── services.py               # write-side use cases: transactional, authorised, audited
├── policy.py                 # can_<action>(user, obj) predicates for this module
├── forms.py
├── views.py                  # thin: parse -> authorise -> call service -> render
├── urls.py
├── admin.py                  # Django admin for master data only, never for operational data
├── migrations/
└── templates/<module>/
    ├── ...page templates...
    └── partials/             # HTMX fragment templates
```

Two modules deviate deliberately:

```text
calculations/                 # no models.py, no views.py, no migrations — pure domain
├── units.py                  # Hz / MHz conversion, Decimal context, rounding policy (A-09)
├── ranges.py                 # half-open RF and time interval algebra (A-10, A-11)
├── bandwidth.py              # symbol rate <-> occupied BW, guards, allocated BW
├── translation.py            # payload translation, spectral inversion
├── conversion.py             # equipment RF <-> IF, LO, sideband
├── placement.py              # gap engine, Auto-place ranking (§16)
├── validation.py             # rule objects producing Blocking / Warning / Info results
└── types.py                  # frozen dataclasses: RfInterval, Allocation, Proposal, ...

spectrum/
├── models.py                 # SpectrumReservation only
├── services.py               # reservation write path, called only by satnet_paths.services
├── selectors.py              # occupancy queries feeding the gap engine and spectrum view
└── views.py                  # spectrum map (read-only; no direct reservation editing, §13.11)
```

### 3.1 Why `selectors` / `services` rather than fat models

Three requirements force it:

- §11 requires **one** calculation engine shared by UI, API and importer. A service layer is the only
  place both the wizard and the importer can meet.
- §15.6 defines a transaction boundary spanning six objects across four modules. That boundary has to
  live somewhere that is not a model's `save()`.
- §12 requires backend authorisation, which needs the acting user — something `Model.save()` does not
  have.

---

## 4. `config/`

```text
config/
├── settings/
│   ├── base.py               # everything shared
│   ├── local.py              # docker-compose development
│   ├── test.py               # fast hashers, no whitenoise, deterministic clock
│   └── production.py         # TLS, secure cookies, HSTS, structured logging (§21)
├── urls.py
├── asgi.py
└── wsgi.py
```

Secrets are read from the environment only (§21.8); no secret ever has a source-code default. A
`django-environ`-style loader validates required variables at boot and fails fast rather than falling
back.

---

## 5. `tests/`

§19.5 places tests at the top level, so they live there rather than per-app.

```text
tests/
├── conftest.py               # fixtures: users per role, seeded beam, frozen clock
├── factories/                # factory_boy factories, one module per domain module
├── domain/                   # pure calculation tests (no DB) + Hypothesis properties
│   └── golden/               # OQ-22 worked FWD/RTN examples — empty until confirmed
├── db/                       # constraint tests: exclusion, checks, triggers, concurrency
├── services/                 # use-case tests incl. transaction rollback
├── permissions/              # §25 permission matrix, one test per matrix cell
├── views/                    # HTTP-level tests, HTMX fragments, 403 on unauthorised POST
├── ui/                       # terminology, English-only, accessibility of the info popover
└── imports_exports/          # dry-run classification, commit idempotency, formula injection
```

`tests/db/test_concurrency.py` needs two real connections and therefore runs against a live
PostgreSQL — `pytest.mark.django_db(transaction=True)`, never SQLite. There is no SQLite path in this
project at all: exclusion constraints, `int8range` and `btree_gist` do not exist there.

---

## 6. Toolchain

| Concern | Choice | Note |
|---|---|---|
| Python | 3.12 | §19.2 |
| Framework | Django 5.2 LTS | §19.2 |
| Database | PostgreSQL 16+, `btree_gist` extension | Extension enabled by the first migration |
| Driver | psycopg 3 (binary in dev, C in the image) | |
| Frontend | HTMX 2.x, Bootstrap 5.3, ECharts — all vendored into `static/` | §19.4 forbids CDN-only |
| Excel | openpyxl | §19.2 |
| Dependency management | `uv` with `pyproject.toml` + committed `uv.lock` | §19.2 "pin dependencies in lock files" |
| Test | pytest, pytest-django, Hypothesis, `pytest-xdist` off for DB-constraint tests | §19.2 |
| Lint/format | Ruff (lint + format) | §19.2 |
| Types | mypy with `django-stubs`, strict on `calculations/` | §19.2 |
| Import rules | `import-linter` contracts matching §1 above | Enforces module boundaries |
| Security | `pip-audit`, Trivy image scan, Bandit | §21.9 |

### Deliberate omissions (§19.4)

No Redis, no Celery, no Kafka, no GraphQL, no SPA, no Kubernetes, no NoSQL. Background work in the
MVP — import commit, export generation — runs synchronously inside the request with a progress
fragment, or via a management command for large batches. If a proven need appears, an ADR precedes
the dependency.

---

## 7. Runtime topology

```text
                     :443 TLS
                        │
                    ┌───▼────┐
                    │ nginx  │  static/, TLS termination, upload limits
                    └───┬────┘
                        │ unix socket
                  ┌─────▼──────┐
                  │  gunicorn  │  Django 5.2, sync workers
                  └─────┬──────┘
                        │ internal network only
                  ┌─────▼──────┐
                  │ PostgreSQL │  named volume, never published to host (§22.2)
                  └────────────┘
```

`compose.yaml` (development) additionally seeds demo users and fixtures so that §22.1 holds: **one
command** brings up Django, PostgreSQL, the four demo roles, seed data and passing health checks.

```bash
make up          # build, migrate, seed, health-check — the single command of §22.1
make test        # pytest against a real PostgreSQL
make check       # ruff + mypy + import-linter + terminology test
```

---

## 8. `docs/adr/` — the 17 mandatory ADRs (§19.6)

Numbered, created in the slice that first makes the decision real, never retro-fitted at the end:

```text
0001-modular-monolith.md                 0010-central-calculation-engine.md
0002-django-htmx-server-rendered.md      0011-specification-dictionary.md
0003-integer-hz-decimal-rolloff.md       0012-master-data-versioning.md
0004-beam-root-pool-satnet-child.md      0013-append-only-audit.md
0005-satnet-path-terminology.md          0014-on-air-revisions.md
0006-two-sided-reservations.md           0015-import-dry-run.md
0007-postgres-exclusion-constraints.md   0016-guard-policy-hierarchy.md
0008-half-open-ranges.md                 0017-suspended-reservation-policy.md
0009-calculated-free-capacity.md
```

---

## 9. Guard rails wired into CI

| Check | Fails the build when |
|---|---|
| `tests/ui/test_terminology.py` | The strings `carrier` or `interference domain` appear in any template, URL, model, migration, fixture or user-facing string (case-insensitive, allow-listed only in `docs/design/`) — §26.5, §26.9 |
| `tests/ui/test_language.py` | A non-ASCII Turkish character or a known Turkish stop-word appears in a user-facing string — §1, §26.1 |
| `import-linter` | A module imports against the dependency direction in §1 |
| `mypy --strict calculations/` | The pure domain layer loses type coverage |
| `tests/db/` | Any DB-level constraint is missing or a race admits two commits — §26.14, §26.15 |
| `pip-audit`, Trivy | A known vulnerable dependency or base image ships — §21.9 |
