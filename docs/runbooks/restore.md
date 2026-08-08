# Runbook — Restore, and the drill that makes a backup real

**Specification:** §22.4
**Commands:** `manage.py verify_restore`, `manage.py restore_drill`

---

## The drill — run this monthly, not when you need it

A restore performed for the first time during an incident is a restore performed for the first
time. §22.4 asks for a verified restore, and the difference between a backup and a file is that
somebody has loaded it and checked the result.

```bash
docker compose -f compose.production.yaml exec web \
    python manage.py verify_restore \
        --dump /backups/vsat-20260808T0200Z-nightly.dump \
        --into vsat_restore_drill
```

Four steps, in this order, and the order is the point:

1. **The SHA-256 is checked against the manifest** — before `pg_restore` is asked to open the
   file. A truncated transfer is the most common way a backup fails, and finding out from a
   restore error halfway through is finding out too late.
2. **The archive is restored into the named scratch database.** Never the live one: the command
   refuses outright if `--into` names the database it is connected to, because a drill that
   overwrites its own source proves nothing and destroys the thing it was checking.
3. **The drill runs against the restored database**, in a subprocess pointed at it.
4. **Every check is reported**, and a failure exits non-zero.

Add `--as <username> --password <…>` to make the sign-in check real. Without them the drill
verifies that the sign-in page renders and that an active administrator with a usable
credential exists, and **says so in its output** — a drill that quietly did less is worse than
one that did less loudly.

## What the drill checks, and why each one is there

| Check | The failure it catches |
|---|---|
| **schema** | The archive is from a different migration state than the code. Legitimate on purpose; a disaster by accident. |
| **row counts** | The archive loaded and half the plan is missing. Compared **at least as many**, not exactly: the manifest's counts are read just before the dump starts so a busy database can grow between them — but nothing here hard-deletes (§20), so it can never shrink. |
| **audit trail** | Every write in this product records an event, so an empty trail means a dropped table. It also reports the age of the newest event, because a restore whose latest event is from last year is almost always an older archive than intended. |
| **sign-in** | A dump missing `auth_permission` or the role groups restores cleanly, renders every page for an existing session, and lets nobody in. |
| **beam detail**, **satnet path detail** | Real pages, through the real URLconf. A restore with a dangling foreign key or a missing generated column counts perfectly and fails on the first page somebody opens. |
| **export** | End to end: the export reads the table's selector, the Specification Dictionary and the scope tables, so a workbook of the right size exercises more of a restored database in one call than anything else. |

**The drill writes nothing.** Signing in creates a session, so the whole run is one transaction
that is rolled back — which is also what makes it safe to point at a restored copy of
production, and what lets the test suite exercise the drill itself rather than trust it.

## Afterwards

```bash
docker compose -f compose.production.yaml exec db dropdb -U "$POSTGRES_USER" vsat_restore_drill
```

A failed drill leaves the scratch database **in place** deliberately, so it can be examined. It
is the only copy of the evidence.

Record the result where the next person will look — the drill writes `RESTORE_DRILL_PASSED` or
`RESTORE_DRILL_FAILED` into the restored database's own trail, which is beside the data it was
checking and therefore gone when you drop it. A monthly note in the operations log is what
survives.

---

## A real restore

This is the destructive path. Everything above is a rehearsal; this is the performance.

### 1. Stop writing

```bash
docker compose -f compose.production.yaml stop nginx web
```

nginx first. Stopping the application while nginx is still up gives every user a 502 rather
than a connection refused — which is the difference between "it is down" and "it is broken".

### 2. Back up what is there now, however broken it is

```bash
docker compose -f compose.production.yaml run --rm web \
    python manage.py backup_database --to /backups --label pre-restore
```

**Do not skip this.** The current database is the only copy of everything written since the
archive you are about to restore, and a restore is not reversible. If it will not dump, say so
in the incident record and proceed knowingly rather than by omission.

### 3. Restore into a scratch database and verify it *first*

The same `verify_restore` command as the drill. Restoring straight over production and
discovering the archive was truncated leaves nothing at all.

### 4. Promote

```bash
docker compose -f compose.production.yaml exec db psql -U "$POSTGRES_USER" -c \
    'ALTER DATABASE "vsat" RENAME TO "vsat_before_restore"'
docker compose -f compose.production.yaml exec db psql -U "$POSTGRES_USER" -c \
    'ALTER DATABASE "vsat_restore_drill" RENAME TO "vsat"'
```

A rename, not a drop. The database being replaced stays under a new name until the restore has
been in service long enough to trust — a day, not an hour.

### 5. Migrate if the schema is behind

```bash
docker compose -f compose.production.yaml run --rm web python manage.py migrate
```

Only if the drill's **schema** check reported the archive behind the code. It will have said so
by name.

### 6. Start, smoke, and tell people

```bash
docker compose -f compose.production.yaml start web nginx
docker compose -f compose.production.yaml exec web python manage.py smoke
```

Then say what was lost. An archive from 02:00 restored at 14:00 means twelve hours of
allocations are gone, and the people who entered them are the only ones who can re-enter them.
Naming the window is the single most useful thing in the incident notice; "we have restored
from backup" without it means nobody knows what to check.

## What a restore cannot fix

**A bad migration is not undone by restoring data.** If the schema change was the problem, the
archive predates it and the code does not. Restore, then deploy the previous release
([deploy.md](deploy.md) has the rollback), in that order.

**A leaked dump is not un-leaked.** See [backup.md](backup.md): an archive contains every TOTP
secret. If one has gone somewhere it should not, every second factor in it is compromised, and
the response is to reset them — which is an administrator action per account.
