# Runbook — Backup

**Specification:** §22.4
**Command:** `manage.py backup_database`

---

## The rule this runbook exists for

**A dump that has never been restored is a file, not a backup.** Nothing in this document is
finished until [restore.md](restore.md) has been run against the archive it produced. A backup
schedule with no restore schedule is a schedule for discovering, on the worst day, that the
archives were never loadable.

## Taking one

```bash
docker compose -f compose.production.yaml exec web \
    python manage.py backup_database --to /backups --label nightly
```

It writes two files:

| | |
|---|---|
| `vsat-20260808T0200Z-nightly.dump` | `pg_dump --format=custom` — compressed, and refused whole by `pg_restore` if its header is damaged |
| `…​.dump.manifest.json` | What the database held: schema version per app, row counts, SHA-256, size, PostgreSQL version |

The manifest is what makes the restore drill meaningful. Without it a drill can only say "the
archive loaded"; with it, it can say the restored `satnet_path` table holds the rows the source
held. **Keep the two files together.** An archive whose manifest was lost can still be
restored — it just cannot be verified, and `verify_restore` will say so rather than pretend.

## Schedule

Nightly, plus one **before every release that carries a migration** (§22.3 — the deploy runbook
makes that a numbered step, not a suggestion).

```cron
# 02:00 UTC daily. UTC because everything in this platform is (A-28), including the
# timestamp in the filename.
0 2 * * * cd /srv/vsat && docker compose -f compose.production.yaml exec -T web \
    python manage.py backup_database --to /backups --label nightly >> /var/log/vsat-backup.log 2>&1
```

Every run is an audit event — `BACKUP_TAKEN`, or `BACKUP_FAILED` with the reason. Search the
trail rather than the log file: **a backup that did not happen is the event worth finding**, and
it is the one an unmonitored cron job hides.

```
/audit/?action=BACKUP_FAILED
```

## Where the archives go

Three properties, in order of how often each one is the reason a restore fails:

1. **Not on the disk holding `postgres_data`.** A backup on the volume that just failed is not
   a backup. The compose file mounts `${VSAT_BACKUP_DIR}` from the host for this reason; point
   it at different storage.
2. **Off the host as well.** Anything that destroys the host destroys everything on it. Copy to
   an off-host target after each run and verify the SHA-256 against the manifest on arrival —
   `verify_restore` does that check for you, and a truncated transfer is the most common way a
   backup silently fails.
3. **Treated as a credential store.** See below.

## A dump is a credential store

Not a caveat — an operational instruction. A dump contains:

- **TOTP secrets** for every second factor (§21). Unlike a password hash, a TOTP secret is not
  one-way: anybody holding a dump holds every second factor in it.
- Session keys, recovery-code hashes, and every allocation in the frequency plan.

So the backup directory is `0700`, owned by the account that runs the backup, and archives are
encrypted at rest wherever they are copied to. An archive that leaks is an authentication
bypass, not just a data disclosure.

## Retention

§20 says nothing operational is hard-deleted, and that applies to the platform's own records
rather than to archives. A defensible default until **OQ-19** answers otherwise:

| | Kept |
|---|---|
| Nightly | 14 days |
| Weekly (Sunday) | 13 weeks |
| Pre-release | 12 months |

Pre-release archives are kept longest because they are the ones somebody reaches for when a
migration turns out to have been wrong, and that is discovered weeks later rather than hours.

## When it fails

`BACKUP_FAILED` names the cause. The three that actually happen:

| | |
|---|---|
| `pg_dump is not on PATH` | The command is running somewhere without the PostgreSQL client tools. Run it in the `web` container, which has them. |
| Disk full | The dump is written before the manifest, so a partial `.dump` with no manifest beside it is the signature. Delete it — an archive with no manifest cannot be verified. |
| `pg_dump failed: … permission denied` | The database user cannot read every table. Fix the grant; do **not** switch to a superuser to make the message go away, because the next thing that reads with those privileges will be something else. |

## What this runbook does not cover

**Point-in-time recovery.** A nightly dump means up to 24 hours of allocations can be lost, and
whether that is acceptable is **OQ-19** — nobody has said what the recovery point objective is.
Continuous WAL archiving is the answer if the objective is minutes rather than a day; it is not
configured here, and pretending a nightly dump meets a requirement nobody has stated would be
worse than saying so.
