# ADR-0003 — Integer Hz and Decimal roll-off

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S4 — Independent Inventory (first slice to store a frequency)
**Specification:** §8.4, §14.1, §14.2, §20

## Context

§14.1 is unambiguous: RF and IF frequencies are integer Hz, symbol rate is integer
symbols/second, roll-off is an exact decimal, guards are integer Hz, timestamps are UTC —
and *"Do not use binary floating-point for engineering values."*

The reason is not stylistic. §8.1 makes an allocated interval exclusive to one Satnet
Path, and §8.3 has PostgreSQL enforce that with a range-overlap constraint. Overlap is
decided by comparing interval endpoints, so an endpoint that is approximate makes the
guarantee approximate. `0.1 + 0.2 != 0.3` in binary floating point; two carriers whose
edges touch exactly would sometimes overlap by a fraction of a Hz and sometimes not,
depending on the arithmetic path that produced them.

## Decision

**Storage.** Frequencies and guards are `BigIntegerField` in Hz. Roll-off is
`DecimalField`. Symbol rate is an integer. Timestamps are `timestamptz` in UTC.

`bigint`, not `int`, is a requirement rather than a precaution: a Ka-band uplink near
30 GHz is 3.0 × 10¹⁰ Hz, and the 32-bit signed maximum is 2.147 × 10⁹. A 32-bit column
would overflow on the first real Ka-band frequency anyone entered.

**Entry and display.** Operators work in MHz. The conversion happens in exactly one
place — `inventory.forms.MegahertzField` — in `Decimal`, and the corresponding display
conversion happens in exactly one other place, the `{% spec_value %}` tag, using the
precision from the Specification Dictionary. No template and no JavaScript converts a
frequency.

**Sub-Hz input is refused, not rounded.** A value with more precision than 1 Hz raises a
validation error. Silently truncating would reintroduce exactly the imprecision this
decision exists to remove, and would do so invisibly.

**Decimal, not float, at every boundary.** `Decimal(str(value))` rather than
`Decimal(value)` where a float could arrive; audit serialisation writes `Decimal` as a
string (`audit.services._jsonable`), because a JSON float round-trip would corrupt the
very values the audit trail exists to preserve.

## Consequences

**What this buys.** Interval endpoints are exact, so the PostgreSQL exclusion constraint
compares exact integers and the §26.15 concurrency guarantee means what it says. Equality
of adjacent edges is decidable, which is what makes half-open ranges usable (`[…, 100)`
next to `[100, …)` is provably non-overlapping).

**What it costs.**

Every read that shows a frequency divides by 1,000,000, and every write multiplies. Both
are `Decimal` operations, which are slower than float — irrelevant at this scale, and the
conversion is confined to two functions.

Raw column values are unreadable at a glance: `29145000000` rather than `29145.000`. The
detail screens show Hz explicitly and the dictionary carries the MHz presentation, but
anyone reading the database directly sees Hz. That is the correct trade — the stored value
is the authoritative one.

Arithmetic must not use `/` on integers. Division producing a float is the one easy way to
reintroduce binary floating point, so the calculation engine in S6 uses `Decimal` and
integer division exclusively, and its rounding policy (**A-09**) is applied in one place.

**What it forecloses.** Nothing, but it does require discipline at every new boundary:
the importer (S15) and the exporter (S14) each need the same exact conversion, and both
must use `MegahertzField`'s logic rather than reimplementing it.

## Alternatives considered

**`DecimalField` for frequencies in MHz** — rejected. It is exact, so it satisfies §14.1,
but PostgreSQL range types over `numeric` (`numrange`) have no integer successor function,
which makes half-open adjacency ambiguous: there is no "next value" after 29145.000, so
`[a, b)` and `[b, c)` cannot be reasoned about the way §8.4 assumes. `int8range` has a
discrete successor, and that is what makes the exclusion constraint behave predictably.

**Float with an epsilon comparison** — rejected outright. §14.1 forbids it, and an epsilon
turns a hard guarantee into a tunable one; the correct epsilon for a 30 GHz carrier is not
the correct epsilon for a 950 MHz IF.

**Storing MHz as integer** — rejected. It loses sub-MHz precision entirely, and the §9.5
worked example (`29,145.000–29,155.500 MHz`) already requires kHz resolution.
