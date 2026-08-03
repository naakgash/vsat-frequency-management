# ADR-0001 — Modular monolith

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S1 — Foundation
**Specification:** §19.1, §19.4

## Context

The platform must coordinate spectrum allocation across Beams, Satnets and Satnet Paths
with a strict transactional boundary: a Satnet Path, its calculated engineering values,
its two Spectrum Reservations, an approval record, a hardware reservation and an Audit
Event must all commit or roll back together (§15.6). Overlap prevention is ultimately a
single PostgreSQL exclusion constraint (§8.3), and two concurrent users attempting
overlapping reservations must produce exactly one successful commit (§26.15).

The deployment target is a single on-premises Ubuntu Server 24.04 host (§22.2), operated
by a team that also has RF engineering responsibilities rather than a dedicated platform
team.

## Decision

Build a **modular monolith**: one deployable Django application, one PostgreSQL
database, internally divided into the modules listed in §19.1, with an enforced one-way
dependency direction between them.

Module boundaries are enforced mechanically by an `import-linter` contract in CI, not by
convention. The contract grows with each slice as modules appear.

## Consequences

**What this buys.**

The §15.6 transaction boundary is a single `transaction.atomic()` block. Across services
it would require a distributed transaction or a saga with compensating actions — for a
guarantee that PostgreSQL provides natively for free, and that the specification makes
non-negotiable.

Overlap enforcement stays in one place. The constraint is the authority (§8.3); a
distributed design would have to either route every allocation through one owning
service — reinventing the monolith with network latency — or give up the guarantee.

Operations stay proportionate to the team: one image, one database, one backup, one
restore drill (§22.4).

**What this costs.**

Module boundaries are conventions backed by a linter rather than by process isolation.
A determined shortcut can still violate them, and the linter must be maintained as
modules are added. This is accepted: the cost of a broken contract is a CI failure and a
refactor, whereas the cost of a broken distributed transaction is a spectrum conflict in
production.

Scaling is vertical plus horizontal Gunicorn workers against one database. For the
expected volumes this is ample; **OQ-15** (user and history volumes) will confirm.

**What it forecloses, and how to unwind it.**

If a module ever genuinely needs independent deployment, the enforced dependency
direction means it can be extracted along an existing seam. The seam to preserve above
all others is `calculations/`, which is pure and imports nothing — it is already a
library, not a service.

## Alternatives considered

**Microservices** — rejected. §19.4 lists them as not to be added without proven need,
and the transactional requirement in §15.6 is direct evidence against.

**Modular monolith with a database schema per module** — rejected for the MVP. Exclusion
constraints and composite foreign keys (docs/design/04 §3.2) span what would be several
schemas, and cross-schema constraints add friction with no benefit at this size.
