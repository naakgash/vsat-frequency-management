# Runbook — Incident

**Specification:** §18, §21, §22

---

## First, decide which kind this is

The three have different first moves, and doing the wrong one wastes the time that matters.

| | First move |
|---|---|
| **The application is down or degraded** | [Availability](#availability) — below |
| **An allocation is wrong** | [A wrong allocation](#a-wrong-allocation) — read before changing anything |
| **Credentials or access are suspected compromised** | [Compromise](#compromise) — the audit trail first, not last |

Whichever it is: **the request id joins the log to the trail.** Every log line carries one and
so does every audit event, so a stack trace leads to exactly the events that request produced:

```
/audit/?request=<the id from the log line>
```

Correlating a busy minute by timestamp is how an investigation reaches the wrong conclusion.

---

## Availability

```bash
curl -sS https://<host>/health/ready | jq
docker compose -f compose.production.yaml ps
docker compose -f compose.production.yaml logs --tail=200 web
```

`/health/ready` names the check that failed and nothing else — no hostname, no credential, no
stack trace. `btree_gist` missing is the one worth knowing on sight: the exclusion constraint
that is the last defence against overlapping allocations is built on it, and a database without
it accepts every migration and enforces nothing.

| Symptom | Usually |
|---|---|
| 502 from nginx | `web` is down or still starting. `logs web` — if the entrypoint's `check --deploy` failed, it says which setting. |
| 429 | The rate limit (§21). Legitimate under a script; check whether it is one. |
| `ready` fails on `database` | Postgres is down or the connection limit is reached. `docker compose exec db psql -c 'SELECT count(*) FROM pg_stat_activity'`. |
| Everything slow | Look at the audit trail before the database: a bulk import or a large export is the usual cause, and both are visible as events. |

Restarting `web` is safe and loses nothing — it holds no state. **Restarting `db` is not the
same thing.** Do not, until you have read why it is unhealthy.

---

## A wrong allocation

**Read before writing.** The instinct is to correct the record; the record is the evidence.

1. **Its history** — every change, who made it, what it was before:
   `/audit/history/satnet_paths.SatnetPath/<id>/`
2. **The revision chain** on the Path's own page. §15.4 means an on-air allocation is closed and
   replaced rather than overwritten, so the version that was wrong is still readable — that is
   what makes this answerable at all.
3. **Where it came from.** If the event carries an import batch, the whole batch is one query:
   `/audit/?batch=<id>`, and `/imports/<id>/` shows exactly what the spreadsheet said, row by
   row, including what the platform recalculated and disagreed with.

Then act through the product, not the database:

- On air and wrong → **revise** it (§15.4). The successor is recomputed from current master
  data and the predecessor stays readable.
- Not yet on air → edit it; a `DRAFT` or `PLANNED` allocation is editable by design.
- Should never have existed → cancel or retire it. Nothing is hard-deleted (§20).

**Do not write SQL against `satnet_path`.** Every guarantee in this product — the exclusion
constraint, the reservation rows that hold spectrum, the audit trail, the revision chain — is
maintained by the service layer. A direct update produces a row that looks right and holds no
spectrum, or holds spectrum nothing will ever release.

---

## Compromise

### If an account may be in the wrong hands

```
/audit/?actor=<username>
```

Everything they did, in order. Then:

1. **Deactivate the account** — administration → the user → inactive. Deactivation rather than
   deletion (§20): deleting it would take the history with it, which is the thing you are
   reading.
2. **Reset their second factor** if it may also be compromised. The event names both people.
3. **Read what they changed**, not just what they opened. `/audit/?actor=<username>` with the
   period narrowed, and every allocation event links to its own history.

### If a database dump may have leaked

Treat it as an **authentication bypass**, not only a data disclosure. A dump contains every TOTP
secret, and unlike a password hash a TOTP secret is not one-way — whoever holds the dump holds
every second factor in it.

1. Reset every second factor (administration → each user → remove; they re-enrol on next
   sign-in).
2. Rotate `DJANGO_SECRET_KEY`, which invalidates every session.
3. Force a password change.

`docs/runbooks/backup.md` says why the backup directory is `0700` and encrypted at rest.

### If sign-ins are being sprayed

Already bounded in two places, and both leave evidence:

- `accounts.services` locks an account out after repeated failures (§21.4, §21.5) — visible as
  `USER_LOCKED_OUT`.
- nginx limits attempts per source address, which is what an account-scoped throttle cannot do:
  one password against four hundred usernames never trips it.

```
/audit/?action=USER_LOGIN_FAILED
/audit/?action=MFA_FAILED
```

A run of `MFA_FAILED` against one account is the specific signal that **somebody has the
password and not the phone**. That account's password is compromised even though nobody got in.

---

## Writing it down

The audit trail records what the platform did. It does not record what you concluded, and that
is the part the next person needs. In the incident record:

- the request ids and audit event ids you followed — so the trail can be re-walked;
- **what was lost**, in wall-clock terms, if anything was restored. "Restored from backup"
  without a window means nobody knows what to re-check;
- what you changed, and through which screen;
- what you decided not to do, and why. That is the sentence that stops the next person
  repeating the investigation.
