# ADR-0013 — Append-only audit, enforced by the database

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S2 — Identity, Roles, Scopes and Audit Skeleton
**Specification:** §18, §20, §26.17

## Context

§18 requires an audit trail covering authentication, permission denials, role and scope
changes, inventory and Beam changes, Satnet Path lifecycle, approvals, imports, exports
and critical settings — and states that *"Audit records cannot be edited or deleted
through the application."*

The phrase "through the application" is the crux. An audit trail exists precisely for the
cases where someone with access is doing something they should not, and in this system
that person may well be an administrator with database access. A guarantee that holds
only for well-behaved Python is not the guarantee §18 is asking for.

Three bypasses defeat a Python-level guard, all of them ordinary rather than exotic:
`Model.save()` overrides are skipped by `QuerySet.update()`; both are skipped by
`cursor.execute()`; and all three are skipped by a `psql` session during an incident.

## Decision

`audit_event` is append-only, enforced by a `BEFORE UPDATE OR DELETE` trigger that
raises with SQLSTATE `restrict_violation`. The application additionally exposes no edit
or delete route, and the model declares **only** view permissions — there is no
`add`/`change`/`delete` permission for any role, including Admin.

Supporting decisions:

- **Trigger, not `REVOKE`.** The on-premises deployment runs as a single database role
  that also needs DDL for migrations, so revoking UPDATE and DELETE from that role is not
  available. A trigger holds regardless of which role connects.
- **`PROTECT` on the actor foreign key.** Deleting a user must never delete their
  history. Users are deactivated, not deleted (§20).
- **Denormalised `actor_username`.** The record stays readable without a join and stays
  truthful if the account is later renamed.
- **Object reference as `(object_type, object_id)`, not a foreign key.** Audit rows
  outlive the objects they describe. A real foreign key would either block legitimate
  deletion or cascade the history away.
- **Sensitive values redacted on write.** A password hash in a `before`/`after` diff is a
  credential in a table that by design can never be corrected or purged, so redaction has
  to happen on the way in, not on the way out.
- **Authorization is a precondition, not part of the unit of work.** `policy.require`
  records the denial and then raises. If the caller had already opened a transaction, the
  rollback would discard exactly the record §18 most wants kept. Every service function
  therefore authorises *before* opening its transaction, and `_record_denial` logs an
  error if it detects otherwise.

## Consequences

**What this buys.** The guarantee is real against every access path the application has,
including raw SQL. Six bypass routes are tested — ORM save, queryset update, ORM delete,
queryset delete, raw SQL update, raw SQL delete — and all six are refused.

**What it costs.**

A genuine correction becomes impossible. An audit record written with a wrong
`change_reason` stays wrong forever; the remedy is a compensating record, not an edit.
This is the intended trade and it should be stated plainly to operators.

Retention and growth are constrained. Rows cannot be pruned by the application, so a
retention policy needs a migration that drops the trigger, deletes, and restores it — a
deliberate, reviewed, audited act. **OQ-15** (expected history volume) will determine
whether partitioning is needed before that becomes urgent.

Restoring from backup restores the trigger with the table, so a restore drill exercises
the guarantee automatically (§22.4).

**Residual limitation, stated honestly.** A denial raised from inside an already-open
transaction still loses its audit record when that transaction rolls back. The precondition
rule prevents this in the code as written and a test pins it, but the rule is a
convention backed by a log line rather than something the database enforces. If this ever
needs to be airtight, the fix is a dedicated audit connection that commits independently
— deferred as unnecessary complexity until a service genuinely needs to authorise
mid-transaction.

## Alternatives considered

**Application-level `save()` guard** — rejected. Bypassed by `QuerySet.update()` and by
raw SQL, which are the paths that matter.

**Append-only via event sourcing / a separate audit database** — rejected for the MVP.
§19.4 rules out additional infrastructure without proven need, and the transaction
boundary in §15.6 requires audit writes to commit atomically with domain writes, which a
separate database would break.

**Write-ahead log shipping to immutable storage** — a reasonable future hardening for a
regulated environment, and compatible with this decision rather than an alternative to it.
Out of scope until a compliance requirement names it.
