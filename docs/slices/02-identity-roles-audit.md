# Slice S2 — Identity, Roles, Scopes and Audit Skeleton

**Phase:** 1 (Project Foundation)
**Report format:** Root Specification §27

---

## Goal

The four roles of §12 can sign in; capability is enforced in the backend rather than by
hiding buttons; every authentication and authorization event reaches an audit trail that
cannot be edited or deleted; and the object-scope machinery exists ready for the domain
modules that will use it.

## Files created or changed

**audit** — `constants.py`, `context.py`, `models.py`, `services.py`, `middleware.py`,
`apps.py`, `migrations/0001_initial.py`, `migrations/0002_audit_event_immutable.py`

**accounts** — `constants.py`, `models.py`, `policy.py`, `scope.py`, `managers.py`,
`services.py`, `forms.py`, `views.py`, `urls.py`, `admin_urls.py`, `types.py`, `apps.py`,
`migrations/0001_initial.py`, `migrations/0002_seed_roles.py`,
`management/commands/seed_demo.py`

**Interface** — `templates/accounts/login.html`,
`templates/administration/user_list.html`, `templates/administration/user_detail.html`,
role-aware `templates/partials/nav.html`

**Configuration** — `AUTH_USER_MODEL`, request-context middleware, login throttle
settings, import-linter contracts for the new modules, mypy and ruff scoping

**Documentation** — `docs/adr/0013-append-only-audit.md`, this report

## Database impact

| Table | Notes |
|---|---|
| `accounts_user` | UUID primary key, unique required email, `deactivated_at`. Custom from the first migration because swapping `AUTH_USER_MODEL` later is one of the few genuinely painful Django migrations. |
| `accounts_login_attempt` | Backs throttling. Two partial indexes on failures, by username and by address. Separate from audit because it is queried on the login hot path and is prunable, whereas audit is not. |
| `audit_event` | Actor, action, outcome, object reference, `before`/`after` JSONB, request context, import provenance. Seven indexes including two GIN for field-level diff search, and a partial index on failures. |

Four role groups are seeded by migration with the capabilities of the design's matrix.
The seed is idempotent and *authoritative*: it sets each group's permissions to exactly
the matrix, so a capability removed from the matrix is revoked rather than left behind.

Demo users are **not** in a migration. A migration runs in every environment, and would
create accounts with published passwords in production. They are a management command
that refuses to run while `DEBUG` is off unless given an explicit acknowledgement flag.

## Security and permission impact

This is the security slice. What it establishes:

- **One authorization choke point.** `policy.require` checks capability, then scope, then
  records the denial and raises. §18 requires denials to be audited, which is only
  tractable if denial happens in one place. `policy.allows` is the silent variant for
  deciding whether to draw a button.
- **Authorization is a precondition, not part of the unit of work.** Services authorise
  *before* opening a transaction, because a denial recorded inside the transaction is
  rolled back with it — losing exactly the record §18 most wants. `_record_denial` logs
  an error if it ever detects otherwise.
- **Deny by default.** A user with no roles holds nothing; anonymous holds nothing.
- **`is_superuser` is not the Admin role.** A Django superuser is a console escape hatch;
  the Admin role is a business role, and scope bypass keys off the role so that granting
  it stays a visible, audited act.
- **Audit is append-only in the database** (ADR-0013). Six bypass paths tested.
- **Sensitive values are redacted before they are written** to a table that can never be
  corrected.
- **Two-threshold login lockout** — see the correction below.
- **Uniform login errors.** The message is identical whether or not the username exists,
  so the form is not a username oracle. The lockout is evaluated *before* credentials are
  verified, so a locked-out attacker cannot confirm a correct password.
- **Source IP comes from `REMOTE_ADDR`**, never from `X-Forwarded-For`. A spoofable
  header must not become an audit record's source address.

### Correction made during this slice

My first implementation counted recent failures for *username OR source address* against
a single threshold of 5. Manual verification caught the consequence: six failed attempts
against `operator` locked out every account from that address, including `admin`. Behind
NAT or a VPN concentrator — the normal deployment for an on-premises operations tool —
one person's mistyped password would have locked out the entire site.

Replaced with two independent thresholds, because the two attacks are different and one
counter defends against neither well:

| Threshold | Default | Defends against |
|---|---|---|
| Per username | 5 | A targeted attack rotating source addresses |
| Per source address | 50 | Password spraying, where no single account reaches its own limit |

Both are covered by tests, including the specific regression: locking one account must
not lock a different account from the same address.

## Tests added

121 tests total, up from 40. New in this slice:

| File | Covers |
|---|---|
| `tests/audit/test_immutability.py` | ORM save, queryset update, ORM delete, queryset delete, raw SQL update and raw SQL delete all rejected; the record survives every attempt intact; INSERT still works; no write permission exists for any role |
| `tests/permissions/test_matrix.py` | Every capability × role cell, generated from the matrix so a new capability cannot ship untested; no-role users hold nothing; roles are additive; superuser is not Admin |
| `tests/permissions/test_policy.py` | Denial raises and is audited; anonymous denial has no actor; `allows` records nothing; the denial message does not reveal which check failed; denial audit survives when the service authorises first; a denial inside a transaction is logged as an error; an audit backend failure does not swallow the denial |
| `tests/permissions/test_scope_registry.py` | Unregistered models are unscoped; resolvers are consulted; Admin bypasses centrally; anonymous denied; registration idempotent; and a forward guard that fails if a model the design marks as scoped exists without a resolver |
| `tests/permissions/test_url_coverage.py` | Every URL enforces authorization or is allow-listed with a reason; the sweep is non-vacuous; stale allow-list entries fail |
| `tests/permissions/test_administration_views.py` | Every write attempted as a direct POST: operator cannot assign roles, observer cannot escalate their own role, admin can; role changes audited with before/after; denials audited; unknown roles rejected |
| `tests/accounts/test_login_security.py` | Login, logout and failure auditing; lockout after the limit; lockout checked before credentials; **one account locking must not lock another from the same address**; spraying trips the address limit; lockout expires as failures age out; error message is not a username oracle |

### A defect this slice found in the S1 foundation

`tests/test_settings.py` now asserts which settings module the suite runs under. It was
added because the answer turned out to be the wrong one: pytest-django ranks the
`DJANGO_SETTINGS_MODULE` **environment variable above** the `ini` setting, so sourcing
`.env` — which any developer does — silently ran the entire suite against `local`
settings. Consequences were a 22× slowdown from production password hashing (39.5s → 1.8s
once fixed) and, more seriously, a suite that on a machine configured for production
would have exercised production settings while appearing green. Fixed by passing `--ds`
in `addopts`, which outranks both, and pinned by two tests.

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.16 — calculated values read-only for normal operators | **Mechanism in place**: the policy choke point and the four enforcement layers. The derived fields it will protect arrive in S11. |
| §26.17 — seed, operational and audit data internally consistent and traceable | **Partial**: authentication, authorization and role changes are traceable end to end. Domain traceability follows its own slices. |
| §26.18 — tests, README, ADRs, runbooks updated | **Partial**: ADR-0013 added; runbooks remain S17. |

Criteria §26.1, §26.5 and §26.9 remain enforced by the S1 guard rails, which now also
sweep this slice's templates and views.

## Verification performed

```
pytest                                        121 passed in 2.24s
ruff check . / ruff format --check .          clean, 60 files
mypy config operations accounts audit         no issues in 38 source files
lint-imports                                  4 contracts kept, 0 broken
manage.py migrate                             clean on a fresh database
manage.py seed_demo                           4 accounts created
```

Exercised against a running server, signed in as real users:

```
anonymous  GET /administration/users/   302 -> /accounts/login/?next=...
observer   GET /administration/users/   403
admin      GET /administration/users/   200, four users with role badges
operator   x6 wrong password            locked out on the 6th
approver   login from the same address  302 (succeeds — the NAT regression)
```

## Deviation from the slice plan

The plan placed `UserBeamScope`, `UserHubScope` and `UserGatewayScope` in this slice.
They are not here, deliberately: their foreign keys point at Gateway, Hub and Beam, which
arrive in S4 and S8. Creating them now would either require placeholder models or invert
the module dependency direction, since `accounts` must not import domain modules.

Instead this slice delivers the scope **machinery** — a resolver registry that domain
modules populate from their own `AppConfig.ready()`, plus `ScopedQuerySet.for_user`. The
three tables land with their targets. `tests/permissions/test_scope_registry.py` carries
a forward guard that fails the build if one of those models ever exists without a
registered resolver, so the deferral cannot be quietly forgotten.

This turned out better than the original plan rather than merely acceptable: the registry
is what lets the `accounts does not import domain modules` import contract hold at all.

## Remaining open questions

Touched, not resolved:

- **OQ-11** (mandatory second-person approval) — no approval flow yet; the setting lands
  with S12.
- **OQ-15** (user and history volumes) — audit indexes are in place; no partitioning or
  retention policy, which ADR-0013 notes will need a deliberate migration.
- **OQ-16** (local vs LDAP/AD) — `AUTHENTICATION_BACKENDS` is the extension point; local
  only.
- **OQ-30** (conjunctive vs disjunctive scope; Gateway grant cascading to Hubs) — the
  registry is agnostic; the resolvers that encode the answer are written in S4 and S8, so
  this is the natural moment to confirm it.

Unchanged and still required before **S9**: **OQ-25**, **OQ-26**, **OQ-27**.

## Next slice

**S3 — Specification Dictionary and the Info Popover.** Admin-managed display metadata
for specification codes, one reusable accessible popover component, and a template sweep
asserting no specification description is hard-coded anywhere else.
