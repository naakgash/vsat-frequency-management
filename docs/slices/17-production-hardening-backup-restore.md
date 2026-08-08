# Slice S17 — Production hardening, backup and restore

**Phase:** 8
**Report format:** Root Specification §27

---

## Goal

The §21 and §22 requirements **implemented, tested and documented, not merely described**: a
production stack where only 443 is reachable, a second factor on the accounts that can change
everything, backups that carry enough with them to be verified, and a restore drill that runs
the real screens against the restored database rather than counting rows and calling it proved.

## Files created or changed

**operations** — `backup.py`, `drill.py`, `constants.py`, and `management/commands/`
(`backup_database`, `restore_drill`, `verify_restore`, `smoke`)

**accounts** — `mfa.py`, `mfa_services.py`, `mfa_views.py`, `middleware.py`, two models,
`migrations/0016`; the sign-in view becomes two steps

**audit** — `context.RequestIdFilter`, so every log line carries the id its audit events carry

**config** — the MFA settings, the middleware, the logging filter; `test.py` turns MFA off with
a stated reason

**Deployment** — `compose.production.yaml` (new), nginx rate limits, `Makefile` targets, CI

**Interface** — `templates/accounts/mfa_{setup,verify,recovery_codes}.html`; a second-factor
panel on the user administration page

**Documentation** — `docs/runbooks/{deploy,backup,restore,incident}.md`, this report

**Tests** — `tests/operations/test_{backup_and_restore,production_posture,scale}.py` (54),
`tests/accounts/test_mfa.py` (46)

## Database impact

Two tables, both about authentication and neither about the frequency plan.

| Table | Holds |
|---|---|
| `accounts_mfa_credential` | One account's TOTP secret, whether it is **confirmed**, and the last counter accepted |
| `accounts_mfa_recovery_code` | Single-use codes, hashed with the project's password hasher; used ones are kept |

`confirmed_at` is the field that matters most: a secret generated and never scanned is not a
second factor, and treating it as one would lock somebody out of their own account for opening
a page and closing it. `last_counter` is the anti-replay record — see below.

## The three things this slice is really about

**A backup that has never been restored is a file.** So every dump is written with a
**manifest** beside it — schema version per app, row counts, SHA-256, size — and `verify_restore`
checks the digest *before* `pg_restore` opens the archive, restores into a named scratch
database, and runs the drill against it. The drill fetches real pages through the real URLconf,
signs in through the real form and runs the real export, because a restore that loaded every row
and left a foreign key dangling passes a `SELECT COUNT(*)` and fails the first page somebody
opens.

The drill **writes nothing** — the whole run is one transaction that is rolled back. That is
what makes it safe to point at a restored copy of production, and it is also what makes the
drill itself *tested* rather than merely written: the suite runs it against the test database,
seven checks and all.

**A second factor that can be declined is a preference.** An administrator owns the inventory,
the Beam engineering, users, scopes and imports; an attacker holding one password can rewrite
the frequency plan and grant themselves the scope to do it again tomorrow. So `LoginView` stops
short of `login()` when a factor is confirmed — nothing is authenticated between the two steps —
and `RequireMfaMiddleware` sends an un-enrolled administrator to enrolment on **every** page,
leaving them exactly two things they can reach: enrolment and sign-out.

Redirect rather than refuse, deliberately: refusing the sign-in would lock out every
administrator the moment this is switched on, including the one who would switch it off.

**What §22.2 requires is asserted, not written down.** "PostgreSQL is unreachable from outside
the compose network" is one `ports:` line away from being false, and that line is easy to add
during a debugging session and easy to forget to remove. `test_production_posture.py` parses
`compose.production.yaml` and the nginx configuration and checks the properties: no published
port on `db` or `web`, only nginx on the public network, only 80 and 443, `internal: true`,
a tagged image rather than a build, no source bind-mounted, and no `migrate` in the container's
command or entrypoint.

## Anti-replay, and why the counter is stored

A TOTP code is valid for thirty seconds. Somebody who reads one over a shoulder has half a
minute to use it, and a naive verifier accepts it every time inside that window.

`mfa.verify` therefore returns **the counter that matched** rather than a boolean, and
`verify_code` stores it and refuses anything at or below it. A code is good once. The test that
pins this is `test_the_same_code_cannot_be_used_twice`, and its neighbour
`test_the_confirming_code_cannot_then_sign_in` pins the consequence: the code that turned the
factor on is spent like any other.

## Recovery codes

Ten, single-use, hashed with the project's password hasher — a recovery code *is* a credential,
and storing it readably would put the whole second factor back into the database dump it exists
to survive. Drawn from an alphabet with no `0/O` and no `1/I/l`, because a code is read off
paper and typed.

Used codes are **kept rather than deleted**: "this account was recovered on the 3rd" is
something an investigation needs, and a row that vanishes when it is spent takes that with it.

## Every log line carries its request id

`audit.context.RequestIdFilter` stamps the same value `audit_event.request_id` holds. An
incident starts with a log line and ends in the audit trail, and this is the only thing that
joins the two — `/audit/?request=<id>` leads from a stack trace to exactly the events that
request produced and to none of the thousands it did not. Correlating a busy minute by timestamp
is how an investigation reaches the wrong conclusion.

## Scale, without a timing assertion

`tests/operations/test_scale.py` asserts **query counts and slope**, never wall-clock time. A
timing assertion fails on a busy runner and passes on a fast one, so it gets a wider bound, and
then a wider one, until it asserts nothing. What actually degrades a Django read path at scale
is a query per row, and that is exactly pinnable: the gap engine, the Satnet Path table, the
spectrum view, the dashboard, the audit search and the export are all asserted flat between 3
rows and 23. A path that is flat at 23 is flat at 23 000; one that is not was going to fail
whatever number was chosen.

The gap engine also gets a correctness check at scale: with allocations placed across the
assignment, **no reported gap may overlap a reservation**. A gap engine that reports occupied
spectrum as free is worse than one that reports nothing.

## Security and permission impact

- **No new capability.** MFA is not an authorisation decision — it is an authentication one, and
  making it a capability would let it be granted away.
- **Rate limits at nginx**, in two zones. The sign-in form is held tighter than everything else,
  and it is the *second* line: `accounts.services` already throttles and locks out per account
  (S2). What nginx adds is a bound per **source**, which an account-scoped throttle cannot give —
  spraying one password across four hundred usernames never trips it. A limited client gets
  **429**, not nginx's default 503, because "the server is broken" sends people to the wrong
  place.
- **Every second-factor action is audited**, including failures. A run of `MFA_FAILED` against
  one account is the specific signal that somebody has the password and not the phone.
- **A reset names both people.** "Who removed whose second factor" is the question that record
  exists to answer, and an event naming only the subject leaves out the interesting half.
- **The TOTP secret is stored, and a dump contains it.** Stated rather than glossed, in
  `accounts/mfa.py` and in the backup runbook: unlike a password hash a TOTP secret is not
  one-way, so a leaked archive is an **authentication bypass**, not only a disclosure. The field
  name carries "secret", so `audit.services` redacts it and it never reaches the trail — a test
  asserts that.

## A bug this slice's tests found

`confirm_enrolment` was `@transaction.atomic`, recorded `MFA_FAILED` on a wrong code, and then
raised — so the raise rolled the audit record back, and the one event §18 most wants (somebody
failing a second factor) was the one that never survived. `accounts.policy` warns about exactly
this shape and it was reproduced two modules away. The check now runs outside the transaction and
only the writes are wrapped.

## Tests added

1355 total, up from 1242. 113 new.

| File | Covers |
|---|---|
| `test_backup_and_restore.py` (27) | The manifest round trip and its forward compatibility; row counts skipping a table the restored schema predates; **an altered archive refused with both digests**; a dump with no manifest saying what is missing; a restore refusing the connected database and an unnamed target; **all seven drill checks passing on a healthy database**; **the drill writing nothing**; a short row count, a schema behind the manifest, a wrong password, no administrator and an empty trail each failing it; a grown database still passing; the sign-in check saying when it did not sign in; `backup_database` end to end against real PostgreSQL with its audit event; the drill failing the command when a check fails |
| `test_mfa.py` (46) | Who needs one (admin, superuser, everybody-if-configured, not an Operator, not anonymous) and **that the production setting still says so**; enrolment not counting until confirmed; a replaced unconfirmed secret; a confirmed factor never re-enrolled; **the secret never reaching the trail**; the QR rendered locally; **a password alone not signing in**; the second step completing it; a wrong code leaving the session unauthenticated; a pending sign-in expiring; **the same code refused twice** and the confirming code spent; an adjacent window accepted and a distant one refused; recovery codes single-use, case- and hyphen-forgiving, hashed, kept when used, and invalidated by reissue; reset by another administrator, named in the event, refused to an Operator; **the middleware redirecting on every page** while leaving enrolment, sign-out and the health endpoints reachable |
| `test_production_posture.py` (20) | **No published port on `db` or `web`**; only nginx public, only 80 and 443; `internal: true`; a tagged image; no bind-mounted source; **no `migrate` in the container**; port 80 redirect-only; TLS 1.2/1.3 only; HSTS at both layers; the sign-in rate limit and its rate; 429 not 503; the upload ceiling matching at both layers; `server_tokens off`; DEBUG not configurable; secure cookies; **the production settings refusing to import without a host list**; the request id in every log line; the security logger not turned down with everything else |
| `test_scale.py` (7) | The gap engine flat in the number of allocations, and its gaps never overlapping a reservation; the table, the spectrum view, the dashboard, the audit search and the export all flat between 3 rows and 23 |

## Acceptance criteria covered

| Criterion | Status |
|---|---|
| §26.18 — deployment and operations | **Met.** One command starts the stack; the release flow, backup, restore and incident paths are runbooks with commands in them; the posture §22.2 requires is asserted by tests. |
| §26.19 — restore verification | **Met.** §22.4's drill is implemented, executed by the suite, and checks sign-in, a Beam, a Satnet Path, the latest audit event, row counts and an export. |
| §26.17 — traceability | **Advanced.** Every backup, drill, enrolment, verification, failure and reset is an audit event, and every log line carries the request id that joins it to them. |
| §26.16 — permissions enforced in the backend | **Held.** The reset route goes through `policy.require`; the URL-coverage guard rail caught both new routes and both are declared with reasons. |
| §26.20 — no invented RF value | **Held.** |

## Verification performed

```
pytest                                   1355 passed, 5 skipped (the OQ-22 gates)
ruff check . / ruff format --check .     clean
mypy (14 modules, calculations strict)   no issues in 190 source files
lint-imports                             5 contracts kept, 0 broken
makemigrations --check --dry-run         No changes detected
export_intake_templates --check          unchanged
```

## Deviations from the plan

**CI had drifted behind the Makefile.** The workflow type-checked eleven modules and the
Makefile fourteen — `approvals`, `reporting` and `imports_exports` were checked locally and not
on the branch that matters, which is the wrong way round for a gate. The list now lives in one
place (`TYPED_MODULES`) and CI calls `make types` with the tool overridden. Not in the plan;
found while adding to CI, and left unfixed it would have kept drifting.

**MFA is off in the test settings**, with the reason stated there. Almost every test signs in as
an administrator to exercise something unrelated to authentication, and the middleware correctly
refuses an un-enrolled one every page. The cost is that the production value is not exercised by
the bulk of the suite, so it is exercised deliberately: `test_mfa.py` restores it with
`override_settings`, and one test asserts `config.settings.base` still requires it — so switching
it off in tests can never quietly become switching it off everywhere.

**Two runtime dependencies**, `pyotp` and `qrcode`. TOTP is RFC 6238 and a hand-rolled
implementation on a security-critical path is a worse idea than a small well-tested library;
the QR code is rendered as an SVG by the pure-Python factory, so no image library is pulled in
and no CDN is involved (§19.4). Both in `pyproject.toml` and `uv.lock`.

**Monitoring configuration is a runbook rather than a file.** The plan lists "monitoring
configuration"; what to point at it — Prometheus, Zabbix, a cron job and an inbox — is **OQ-17**,
and a scrape configuration for a system nobody has named would be a guess somebody has to
migrate away from. What is delivered instead is the thing any monitor needs: `/health/ready`
naming the check that failed, `manage.py smoke --json` and `restore_drill --json` for a monitor
to read, and the audit actions to alert on, named in the runbooks.

## Remaining open questions

**OQ-19 — the recovery point objective.** A nightly dump means up to 24 hours of allocations can
be lost. Whether that is acceptable is a business decision nobody has made; continuous WAL
archiving is the answer if the objective is minutes, and configuring it against an unstated
objective would be guessing. Stated at the end of `backup.md` rather than silently assumed.

**OQ-17 — the monitoring target and the network's shape.** Also why TOTP was chosen over a
push-based or WebAuthn second factor: TOTP needs no network at verification time and works on a
host with no route to the internet, which is what OQ-17 says this deployment may well be.

**OQ-15 — expected volumes.** The scale tests hold the *slope*; they do not say what the
absolute numbers will be. The audit search's `COUNT(*)` is the first thing that gets slow, and
keyset pagination is the fix — worth doing once somebody says how large the trail gets.

**OQ-22** — still the one gap that cannot be closed by building.

## Next slice

**S18 — Pilot, cutover and final acceptance.** Phase 9: the parallel run, the migration
comparison, and the acceptance criteria signed off against a real plan — which is where OQ-18's
incumbent workbook and OQ-22's worked example stop being open questions and become blockers.
