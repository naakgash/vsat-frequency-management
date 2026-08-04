# ADR-0007 — The database is the final authority on overlap

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S9 — Reservations, the exclusion constraint and the gap engine
**Specification:** §8.1–§8.4, §8.3, §13.11, §20
**Assumptions:** **A-11**, **A-12**, **A-13**, **A-14**, **A-23**

## Context

The platform's central promise is that two allocations cannot occupy the same Hz on the same
resource at the same time. Everything up to this slice *described* spectrum; this is where it
is guaranteed.

§8.3 states the rule: *"Keep PostgreSQL as the final defense layer… The UI and service layer
must perform pre-checks to explain errors, but they must not be the only protection."*

The reason is not defence in depth for its own sake. A service that reads the reservations,
finds no conflict, and then writes has a window between the read and the write in which
another connection can write the conflicting row. **No amount of care in that service closes
the window**, because there is no moment at which the read and the write are one operation.
Two operators placing overlapping transmissions at the same instant both pass every check.

## Decision

**A PostgreSQL `EXCLUDE USING gist` constraint on `spectrum_reservation`.**

```sql
EXCLUDE USING gist (
    spectrum_resource_id WITH =,
    allocated_rf         WITH &&,
    active_period        WITH &&
)
WHERE (reserves_spectrum)
```

The key is `spectrum_resource_id` and nothing else — see ADR-0018, which is where the choice
of key was made and why it is not the Beam.

**`allocated_rf`, not `occupied_rf`.** §8.1 makes the reserved interval the allocated one, and
§8.2 warns against comparing centre frequencies. Guard bands are part of what is held; a
constraint on the occupied range would accept two transmissions whose guards overlap, and the
guards would exist as decoration.

**Half-open ranges, generated from the scalar columns.** `int8range(start, end, '[)')` as a
stored generated column. The bounds are the form-bindable values and the range cannot drift
from them, because it is not maintained by anything. Adjacency is therefore non-overlapping
*by construction* (**A-11**) rather than by application arithmetic that has to remember.

**`WHERE (reserves_spectrum)` over a stored boolean.** A partial index predicate must be
`IMMUTABLE`, and whether a `SUSPENDED` allocation holds spectrum is a runtime setting (§15.3,
**OQ-08**). So the column is written by the service and a CHECK pins every status whose policy
is *not* configurable (**A-12**). `SUSPENDED` is deliberately absent from that CHECK: pinning
it would answer an open question by implication.

**`IMMEDIATE`, not `DEFERRABLE`.** A deferred constraint raises at `COMMIT`, by which point
the failing statement is gone and the error can say only that *something* in the transaction
overlapped. §9.5 requires the refusal to name the conflicting allocation, and with several
occupancy rows per allocation (**A-23**) a commit-time error would be genuinely unhelpful.
Immediate checking also holds locks for less time (**A-14**).

**Fixed reserves live in the same table** (**A-13**). §16 subtracts them from free spectrum,
and an exclusion constraint cannot span two tables — a fixed reserve in its own table could be
overlapped freely by an allocation. `kind` is therefore not in the key.

**There is no write route to this table for any role** (§13.11). `default_permissions =
("view",)`, so Django never generates add/change/delete, and a test asserts that. Reservations
are written by `spectrum.services` inside the transaction that creates the allocation they
belong to. A screen that could edit a reservation directly could put it and its Satnet Path
into disagreement, and the constraint would go on enforcing whatever the reservation said.

## Consequences

**What this buys.** A guarantee that holds under concurrency, against the importer, against a
migration, and against a `psql` session — none of which route through any service.
`tests/spectrum/test_concurrency.py` proves it with two real connections and a barrier, and it
exists *before* the feature that writes reservations, which is why S9 precedes S11 in the plan.

**What it costs.**

*The error is an `IntegrityError` with a constraint name.* Turning that into §9.5's message —
which rule, which Beam, which window, which conflicting path, how much overlap — is work the
service has to do by querying for the conflict after the fact. S11 owns that, and it is
strictly harder than reporting the problem from a pre-check would have been. It is also the
only version that is *true*.

*A GiST index on every reserving row.* Larger and slower to update than a btree. This is the
index the whole product depends on being correct, so the cost is not negotiable, and the key
dropping from six columns to three (ADR-0018) made it materially smaller.

*Constraint-bearing tables are expensive to migrate.* This is why OQ-25 and OQ-27 held the
gate: altering the key of a populated `spectrum_reservation` means taking the guarantee away
while the change runs.

**What it forecloses.** Any lifecycle state that "sort of" holds spectrum. A row either
reserves or does not, and `reserves_spectrum` is a boolean, so a status meaning "holds it
against some allocations but not others" would need a second key column rather than a third
value.

## Alternatives considered

**A service-layer check with `SELECT … FOR UPDATE`.** Rejected. It requires knowing what to
lock *before* knowing what conflicts — the row does not exist yet — so it degrades to locking
the resource, which serialises every allocation on a shared payload input and still relies on
every write path remembering to take the lock.

**`SERIALIZABLE` isolation.** Rejected. It would work, and it moves the failure to a
serialisation error that says nothing about frequencies, retried by the application on
transactions that may have been legitimately refused. The constraint says exactly what was
wrong.

**A unique index on a discretised frequency grid.** Rejected outright: it would answer §8.4's
half-open ranges with a raster, and the raster size is **OQ-31** — unanswered, and not
something to invent inside the platform's central guarantee.
