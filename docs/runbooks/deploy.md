# Runbook — Deploy

**Specification:** §22.1, §22.2, §22.3
**Commands:** `manage.py migrate`, `manage.py smoke`

---

## The shape of a release

§22.3 makes these separate, reviewed steps rather than one command, and the separation is the
whole design: **migrations are never applied by a container starting up.** `docker/entrypoint.sh`
deliberately does not run them, because auto-migrating on start applies a schema change the
moment a container restarts — with no review, no backup, and nobody watching.

```
1. Review the diff, including every migration in it
2. Back up            → docs/runbooks/backup.md
3. Pull the image
4. Apply migrations   → a separate, watched step
5. Restart
6. Smoke              → manage.py smoke
7. Say it is done
```

## 1. Review

Read the migrations. Specifically:

- Does any of them **drop or rename** a column? §20 says nothing operational is hard-deleted;
  a migration that removes a column is removing history, and it needs a reason in the pull
  request rather than a shrug.
- Does any of them add a **constraint** to a populated table? PostgreSQL takes an `ACCESS
  EXCLUSIVE` lock to validate one. On `satnet_path` that is an outage for its duration.
- Does any of them add an **index** without `CONCURRENTLY`? Same lock, same consequence.

## 2. Back up

Not optional, and not "the nightly one from this morning". [backup.md](backup.md), with
`--label pre-release`. This is the archive somebody reaches for if the migration was wrong, and
it is kept for twelve months for that reason.

## 3. Pull

```bash
export VSAT_IMAGE=ghcr.io/…/vsat-spectrum:1.4.0
docker compose -f compose.production.yaml pull web
```

**A tag, never `latest`.** `compose.production.yaml` requires `VSAT_IMAGE` to be set and refuses
to start without it, so "which version is running" is always answerable. The image is what CI
built and scanned; nothing is built on the production host.

## 4. Migrate

```bash
docker compose -f compose.production.yaml run --rm web python manage.py migrate
```

`run --rm`, not `exec`: a one-off container, so this is visibly a step somebody took rather
than something a long-running process did on its own.

Watch it. If it hangs, it is waiting on a lock — check `pg_stat_activity` before killing it,
because a migration interrupted midway leaves the schema in a state neither release expects.

## 5. Restart

```bash
docker compose -f compose.production.yaml up -d
```

The entrypoint runs `collectstatic` and then `check --deploy --fail-level WARNING`. **That check
is an assertion, not advice**: a deployment with `DEBUG` on, insecure cookies or no HSTS refuses
to serve rather than serving insecurely. A container that will not start after a settings change
is that check doing its job — read its output rather than removing the flag.

## 6. Smoke

```bash
docker compose -f compose.production.yaml exec web python manage.py smoke
```

Read-only and anonymous, so it is safe against production: the health endpoints, the sign-in
page, static files, and — the one that matters — **a protected page correctly refusing an
anonymous visitor**. That is what catches a deployment served with the wrong settings module,
which no health endpoint would notice.

CI runs the same command on every branch, so a release never meets it for the first time here.

## 7. Say it is done

Include the image tag and the migrations applied. "Deployed" without them is a message somebody
has to reply to.

---

## Rolling back

**Code rolls back. Schema usually does not.**

```bash
export VSAT_IMAGE=ghcr.io/…/vsat-spectrum:1.3.0
docker compose -f compose.production.yaml up -d web
docker compose -f compose.production.yaml exec web python manage.py smoke
```

That is the whole rollback **if the release carried no migration**. If it did, the previous
image is meeting a schema it does not know:

- A migration that only **added** things is usually safe to leave in place. Roll the code back
  and leave the schema forward.
- A migration that **changed or removed** something is not. Restore the pre-release archive
  ([restore.md](restore.md)) and then roll the code back, in that order.

Django's `migrate <app> <number>` reverses a migration where one is reversible, and a reversed
data migration is a data migration running backwards — which is a thing to do knowingly, on a
scratch database first, not at speed during an incident.

## What the deployment guarantees, and where each is enforced

| §22.2 requires | Enforced by |
|---|---|
| Only 443 published | `compose.production.yaml` — nginx is the only service with `ports:`, and 80 exists solely to redirect |
| PostgreSQL unreachable from outside | The `internal: true` network, and the deliberate **absence** of a `ports:` entry on `db`. The development file publishes 5432 on the loopback; that line is gone here rather than commented, so it cannot come back by deleting a `#` |
| TLS, HSTS | nginx terminates TLS; Django sets HSTS as well, so the header survives a request that never reaches the application |
| Rate limits | `docker/nginx/vsat.conf` — a tight zone on the sign-in form, a burst ceiling elsewhere |
| Second factor for administrators | `accounts.middleware.RequireMfaMiddleware`, on every page |
| No secret in the repository | `config/env.py` — required values have no default and fail at boot |
| Dependency and image scanning | CI: `pip-audit` on the lock file, Trivy on the built image |

## First deployment

```bash
cp .env.example .env      # then fill in every value; several have no default by design
docker compose -f compose.production.yaml up -d db
docker compose -f compose.production.yaml run --rm web python manage.py migrate
docker compose -f compose.production.yaml run --rm web python manage.py createsuperuser
docker compose -f compose.production.yaml up -d
```

The first administrator to sign in is sent straight to second-factor enrolment and can reach
nothing else until it is done (§21). That is intended: the account that owns everything should
not exist with a password alone even for an afternoon.

**The inventory ships empty.** No satellite, band, window, translation, polarization mapping or
guard value — each is an unresolved RF engineering question (§26.20), and a plausible-looking
invented one would be indistinguishable from real data once loaded. `docs/rf-confirmation/` is
how they are asked for. A deployment with no inventory is correct, not broken.
