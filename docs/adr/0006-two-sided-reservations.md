# ADR-0006 — A Satnet Path reserves both sides, and one is the image of the other

**Status:** Accepted
**Date:** 2026-08-04
**Slice:** S7 — Translation, IF Conversion, Equipment Matching
**Specification:** §8.1, §9.3, §13.5, §13.7
**Assumptions:** **A-03**, **A-10**
**Open questions:** **OQ-02**, **OQ-26**, **OQ-28**

## Context

A Satnet Path is direction-specific: a forward path runs hub uplink to remote downlink, a
return path runs remote uplink to hub downlink (**A-03**). It therefore occupies spectrum
in **two** places, and §8.1 makes each of those exclusive to one path. Both have to be
reserved, or the platform is protecting half of what it allocates.

That much is obvious. The question this record settles is how the second side is
*obtained*, and there are two candidates that look equivalent and are not:

1. compute each side independently from its own centre frequency; or
2. compute one side, and derive the other as the **image** of it under the payload
   translation.

They differ by up to one Hz, and one Hz is enough. The occupied range is built from a
half-width rounded outward (**A-09**), so an odd bandwidth produces a range one Hz wider
than the computed bandwidth. Round that independently on each side and the two can differ —
an allocation that fits its uplink Window exactly would fail containment on the downlink,
for reasons no screen could explain.

## Decision

**One side is calculated; the other is its image.** `bandwidth.place_both_sides` places the
entered side once and moves the two resulting intervals whole:

```python
uplink   = place(request, centre_hz, guards)
downlink = translate(uplink.occupied), translate(uplink.allocated)
```

§13.7's translation preserves width exactly, so the far side inherits the near side's
rounding rather than repeating it. Everything except position is shared: the symbol rate,
the roll-off, the occupied bandwidth and the guards describe the *transmission*, not the leg
it is observed on.

**Either side may be the entered one.** §9.3 lets the canonical entry side differ by
direction (**OQ-28**), so `entered_side` selects `translate` or `untranslate`. The pair is
symmetric — entering the uplink centre or the corresponding downlink centre produces
identical results, which is a test rather than an aspiration.

**Translation is exactly reversible, and that is the load-bearing claim.** An offset inverts
to the opposite offset; a reflection is its own inverse. A Hypothesis property checks
`untranslate(translate(r)) == r` across every method and thousands of intervals.

**Inversion is carried, not derived.** This is the subtle one.

Because translation preserves width, *any* downlink interval reachable by a reflection is
equally reachable by a shift: given `[a, b)` and a same-width `[c, d)`, both `shift(c − a)`
and `reflect(c + b)` produce it. **The pair of intervals contains no evidence of which
happened.** Only the Payload Path knows, so `TwoSidedPlacement.inverted` comes from the
spec and is stored, never inferred from the geometry.

It matters downstream: under inversion the operator's low edge on one side is the high edge
on the other, and a spectrum plot drawn left-to-right on both sides shows the transmission
mirrored.

**A path that claims to invert but carries an offset is refused, not guessed at.** The model
stores `spectral_inversion` independently of the translation method. An offset preserves
frequency order and carries no reflection constant, so there is no arithmetic that could
honour the flag — `validation.check_translation` reports
`INVERSION_WITHOUT_REFLECTION` as an error rather than picking an interpretation that would
look plausible and be wrong.

**Both legs are validated together.** `validation.check_two_sided` prefixes every finding
with `UPLINK_` or `DOWNLINK_`, because "OUTSIDE_WINDOW" on a two-sided result does not say
which window. It also checks that the two widths agree — unreachable through
`place_both_sides`, and checked because a `TwoSidedPlacement` can be rebuilt from stored
columns by the importer.

## Consequences

**What this buys.** The two reservations cannot drift. A change to the rounding policy moves
both sides together by construction. An operator may work from whichever side their process
starts on. The S9 exclusion constraint receives two intervals that are provably images of
one another, so a conflict on one side is a conflict about a real transmission rather than
about a rounding artefact.

**What it costs.**

The far side's centre is not necessarily the translation of the near side's centre when the
bandwidth is odd — the interval moved, and the centre follows from it. That is correct and
it will look surprising to anyone checking by hand. The Engineering Preview shows both
intervals rather than both centres for this reason.

`inverted` is a stored flag on a computed object, which reads like a smell. The alternative
is inferring it from geometry, which is provably impossible; the comment on the field says
so, at length, because the next person to see it will reach for the same reflex.

Only the uplink Window is checked on the Engineering Preview. A downlink Window would need a
second pair of fields, and that belongs with the real Payload Path in S11 rather than on a
sandbox. The two-sided *validator* accepts both windows and is tested with both.

**What it forecloses.** Independently positioning the two sides of one path. If a payload
ever requires that, it is not a translation and the Payload Path model does not describe it.

## Alternatives considered

**Compute each side from its own centre** — rejected, as described. It is the obvious
implementation and it re-rounds, so the two sides can differ by a Hz with nothing to explain
it. This is precisely the class of bug that surfaces months later as "the tool says it fits
and the modem disagrees".

**Store only the uplink and derive the downlink at read time** — rejected. The stored
reservation is what the exclusion constraint indexes (§8.3); a derived downlink cannot be
indexed, so the downlink leg would have no database-level protection at all. It also breaks
the moment a Payload Path is superseded (ADR-0012): the historical allocation would be
re-derived through the *new* translation.

**Model the two sides as two independent Satnet Paths** — rejected. §13.7 makes them one
direction of one path, they share a lifecycle and an approval, and splitting them would let
one be cancelled while the other stayed on air.

**Derive `inverted` from the two intervals** — rejected because it is impossible, not
because it is inconvenient. Recorded here because it is the first thing anyone will try.
