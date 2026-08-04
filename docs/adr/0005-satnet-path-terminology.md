# ADR-0005 — "Satnet Path" is the only name for an allocation

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S11 — Guided Satnet Path creation
**Specification:** §7, §9, §13.10, §26.9
**Assumptions:** **A-19**

## Context

§7 removes a term the industry uses constantly for a direction-specific allocation and replaces
it with **Satnet Path**. The removed word is the one every operator, every vendor document and
every spreadsheet in the incumbent process uses, which makes it the single most likely thing to
reappear — in a column heading, a URL, a form label, a migration, a seed value, or a comment
somebody wrote in a hurry.

S11 is where that pressure peaks: this is the slice that finally builds the thing, and it
touches models, forms, URLs, templates, services and reservations at once.

## Decision

**One term, enforced by a test rather than by review.** `tests/ui/test_terminology.py` scans
every tracked `.py`, `.html`, `.md`, `.sql`, `.yaml` and `.toml` file on every commit, with a
narrow allow-list for the design documents and ADRs that must name the term in order to forbid
it, and for the verbatim RF answer transcripts.

A guard rail rather than a convention, because the failure mode is slow: one migration or one
template reintroduces the word, nobody notices, and six months later half the product uses each
name and neither is wrong enough to fix.

**The entity is `SatnetPath` and the module is `satnet_paths`.** URLs are `/satnet-paths/`,
capabilities are `satnet_paths.view_satnetpath` and `satnet_paths.manage_satnet_paths`, and the
reservation table's `kind` is `SATNET_PATH`. There is no alias anywhere, and nothing translates
between two vocabularies at a boundary — a translation layer is how two names survive.

**A revision is not a new Path.** `revision_group` stays constant across a chain, so "the
allocation" and "this revision of it" are distinguishable without a second noun. §15.4's
history is one indexed query, and the list shows only revisions with nothing superseding them.

## Consequences

**What this buys.** An unambiguous vocabulary in a domain where the ambiguity is expensive:
§8.2 turns on the fact that one allocation occupies spectrum on *two legs*, and a name that
suggests a single carrier of spectrum makes that harder to say. It also makes the Phase 9
comparison against the incumbent spreadsheets legible — a difference in a column called "Satnet
Path" cannot be confused with a difference in what the spreadsheet called something else.

**What it costs.** Every person who arrives from the incumbent process has to learn one word,
and the platform never meets them halfway — searching for the familiar term returns nothing.
The Specification Dictionary (§2) is where that is answered: the code and its meaning are
explained in one place the interface links to.

**What it forecloses.** An import that accepts the old column heading without mapping it.
S15's `import_mapping` table is where the incumbent's vocabulary is translated to this one,
once, explicitly, and visibly — rather than by an alias in the model that would quietly make
both names correct.

## Alternatives considered

**Allow the term in comments and docstrings.** Rejected. A comment is where the next model
field name comes from, and the guard rail's value is that it has no judgement calls in it.

**A property alias on the model for convenience.** Rejected outright: an alias is exactly the
mechanism by which two names both become correct.
