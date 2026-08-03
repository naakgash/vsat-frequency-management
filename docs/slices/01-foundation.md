# Slice S1 — Foundation and Health

**Phase:** 1 (Project Foundation)
**Report format:** Root Specification §27

---

## Goal

Produce a running, English, authenticated-shell application against PostgreSQL 16 with
the extensions the spectrum constraints depend on, health endpoints for monitoring, and
the CI guard rails that keep the specification's terminology and language rules
enforceable for the rest of the project.

This slice deliberately contains no domain model. Its purpose is that every later slice
lands on a foundation where a violation of §1 (English only), §4 (no Interference
Domain), §7 (no `Carrier` label) or §19.4 (no CDN dependency) fails the build
automatically rather than relying on review.

## Files created or changed

**Configuration**
- `pyproject.toml`, `uv.lock` — pinned dependencies (§19.2), Ruff, mypy, pytest and
  import-linter configuration
- `config/env.py` — environment access with fail-fast validation and no secret defaults
- `config/settings/{base,local,test,production}.py`
- `config/{urls,wsgi,asgi}.py`, `manage.py`
- `.env.example`, `.gitignore`, `.dockerignore`

**Application**
- `operations/health.py` — liveness and readiness checks
- `operations/views.py` — health endpoints, landing page, safe error handlers
- `operations/{apps,urls}.py`, `operations/migrations/0001_extensions.py`

**Interface**
- `templates/base.html`, `templates/partials/nav.html`, `templates/home.html`
- `templates/{403,404,500}.html`
- `static/css/app.css`, `static/js/csrf.js`
- `static/vendor/` — Bootstrap 5.3.3, HTMX 2.0.4, with licences and provenance

**Deployment**
- `docker/Dockerfile` (multi-stage, non-root), `docker/entrypoint.sh`
- `docker/nginx/vsat.conf`, `compose.yaml`, `Makefile`
- `.github/workflows/ci.yml`

**Documentation**
- `docs/adr/0001-modular-monolith.md`, `docs/adr/0002-django-htmx-server-rendered.md`
- This report

## Database impact

One migration, `operations.0001_extensions`, creating `btree_gist`, `citext` and
`pgcrypto`. No tables beyond Django's own `auth`, `contenttypes` and `sessions`.

The extensions are installed first, before any domain migration, because later slices
declare columns and constraints that cannot exist without them. All three are *trusted*
extensions in PostgreSQL 13+, so the database owner can install them without superuser
rights — relevant to the production runbook, where the application role should not be a
superuser.

Verified on PostgreSQL 16.13, which is the version the production target ships.

## Security and permission impact

No authentication or authorization exists yet — that is S2. What this slice establishes:

- **Secrets have no source-code defaults** (§21.8). `env.require()` has no `default`
  parameter; a missing `DJANGO_SECRET_KEY` stops the process at boot. Tested.
- **`DEBUG` cannot be enabled in production.** It is hard-coded `False` in
  `settings/production.py`, not defaulted — no environment variable can turn it on.
  Tested.
- **Production requires an explicit `ALLOWED_HOSTS`**; an empty value raises at import.
  Tested.
- Secure cookies, CSRF, HSTS, `X-Frame-Options: DENY`, nosniff and referrer policy are
  set for production (§21.1–21.3); development relaxes only the `Secure` flags, which
  would otherwise prevent login over plain HTTP.
- **Health endpoints disclose nothing.** They are unauthenticated by necessity — they are
  polled before any session exists — so database errors are reduced to an exception class
  name. A test asserts that a connection error containing a hostname, username and
  password reaches the response as `DatabaseError` and nothing more (§21.15).
- Error pages render without stack traces (§21.15).
- The container runs as a non-root user; the entrypoint runs
  `check --deploy --fail-level WARNING`, which refuses to start on a weak `SECRET_KEY`.
- **Migrations do not run on container start.** §22.3 makes migration review a distinct,
  gated release step; auto-migrating would apply a schema change to production on any
  container restart, with no review and no backup gate.
- CI runs `pip-audit` and a Trivy image scan (§21.9).

## Tests added

40 tests, all against real PostgreSQL. There is no SQLite path in this project.

| File | Covers |
|---|---|
| `tests/test_health.py` | Liveness does not touch the database; readiness passes when healthy; 503 with a missing extension; 503 when the database is unreachable; no disclosure of hostnames or credentials; responses are not cached |
| `tests/db/test_extensions.py` | Required extensions installed; PostgreSQL ≥ 16; range constructors are IMMUTABLE enough for generated columns; `btree_gist` supports a mixed equality + overlap exclusion constraint that actually blocks an overlapping row |
| `tests/test_settings.py` | No secret defaults; whitespace rejected; boolean parsing including rejection of nonsense values; production `DEBUG` unforceable; explicit `ALLOWED_HOSTS` required; PostgreSQL backend; UTC storage; cookie hardening |
| `tests/ui/test_terminology.py` | `Carrier` and `Interference Domain` absent from the product, plus a meta-test that the scan is not vacuous |
| `tests/ui/test_language.py` | No Turkish characters or words; detectors proven to fire on real Turkish and to leave technical English (`FWD`, `RHCP`, `BUC`, `roll-off`) alone |
| `tests/ui/test_no_external_assets.py` | No template references an external host; vendored assets present |

Two of these deserve note.

`test_btree_gist_enables_a_mixed_equality_and_overlap_exclusion_constraint` builds a
miniature of the real overlap constraint from docs/design/04 §3 and asserts that
adjacent half-open ranges are accepted, non-reserving rows are outside the partial index,
a different leg is a different scope, and an overlapping row in the same scope is
**refused by the database**. It exists in S1, six slices before the real constraint, so
the foundational assumption behind the entire enforcement design is proven before
anything is built on it.

The guard rails were verified by deliberate violation, not merely by passing: a probe
template containing `Carrier`, `Interference Domain`, Turkish text and a CDN reference
made all five relevant tests fail with file and line numbers, and removing it returned
them to green.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.1 — complete interface is English | **Enforced**, not merely satisfied: the language guard rail fails the build on Turkish text anywhere in the product |
| §26.5 — no Interference Domain entity or user-facing concept | **Enforced** by the terminology guard rail |
| §26.9 — `Carrier` not used as an entity label | **Enforced** by the terminology guard rail |
| §26.18 — tests, README, ADRs and runbooks updated | **Partial**: tests, README and the first two ADRs are in place; runbooks are S17 |

No other acceptance criterion is claimed. Criteria §26.2–26.4 and §26.6–26.17 require
domain models that this slice deliberately does not contain.

## Verification performed

```
pytest                                        40 passed
ruff check .                                  All checks passed
ruff format --check .                         26 files already formatted
mypy config operations                        no issues in 15 source files
lint-imports                                  1 contract kept, 0 broken
manage.py makemigrations --check --dry-run    No changes detected
manage.py check --deploy --fail-level WARNING no issues (production settings)
```

Endpoints exercised against a running server on PostgreSQL 16.13:

```
GET /health/live    200  {"status": "ok"}
GET /health/ready   200  {"status": "ok", "checks": {"database": "pass", "extensions": "pass"}}
GET /                200  English shell, §7 navigation order
```

## Remaining open questions

None were resolved by this slice, and none blocked it. Touched but not answered:

- **OQ-16** (local vs LDAP/AD authentication) — `AUTHENTICATION_BACKENDS` is the
  extension point; only local authentication is wired.
- **OQ-17** (intranet/VPN access policy) — the nginx configuration assumes on-premises
  with only 443 published.
- **OQ-23** (default display time zone) — storage is UTC (§14.1); no display time zone is
  configurable yet.
- **OQ-15** (expected volumes) — no partitioning or index tuning yet.

The three questions that must be answered before **S9** remain open and unchanged:
**OQ-25** (cross-Beam frequency reuse), **OQ-26** (remote-side equipment), **OQ-27**
(Beam sub-ranges). None of them block S2 through S8.

## Next slice

**S2 — Identity, Roles, Scopes and Audit Skeleton.** Custom user model, the four demo
roles, the three scope tables, the `policy.require` choke point, the append-only audit
trigger, and login throttling with temporary lockout.
