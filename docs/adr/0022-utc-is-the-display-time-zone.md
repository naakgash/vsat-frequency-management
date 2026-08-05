# ADR-0022 — UTC is the display time zone, and it is displayed

**Status:** Accepted
**Date:** 2026-08-05
**Slice:** S11a — Controlled hardware references and UTC
**Specification:** §14.1, §14.5
**Answers:** **OQ-23**
**Assumptions:** **A-08**, **A-28**

## Context

Storage has been UTC since S1 — §14.1 requires it, `TIME_ZONE = "UTC"` and `USE_TZ = True` were
set then, and every validity period, exclusion constraint and audit row has been computed in it
since. What was open was **display**, and the register's provisional position was *"display time
zone a system setting, unset until confirmed"*.

The answer removes the setting rather than filling it in:

> UTC shall be the platform's authoritative operational and display time zone… Operational
> screens shall display UTC explicitly. A local time zone may be offered as a secondary
> user-interface display, but it shall not affect validation or stored values.
>
> An `effective_from` value that defaults to the present shall use the current UTC instant and
> shall not be rounded back to midnight.

## Decision

**One filter, `{{ value|utc }}`, and it names the zone.** It converts from whatever the value
carries and renders `2026-08-05 07:15 UTC`. Every operational screen that shows a timestamp uses
it: Satnet Path detail and its blocking findings, Satnet detail, Beam detail and its spectrum
panel, and the inventory screens that show an effective period or a version history.

The word "explicitly" is the requirement. Django renders a timestamp in the *active* zone and
prints no zone name, so `07:15` was telling a reader in Istanbul something different from what
it was telling one in Denver, and telling neither of them which. Appending the zone costs one
filter and removes an entire class of misreading from a product whose whole subject is
intervals.

**A local zone, if it ever arrives, is a second column — never `timezone.activate()`.** This is
the load-bearing part of the decision. Activating a zone per request is the obvious way to offer
a local display and it is the wrong one, because the same activation that formats an output also
**parses an input**: a `datetime-local` field submitted under an active Istanbul zone would be
stored three hours off. The answer forbids exactly that outcome, so the display is deliberately
independent of the active zone, and a test asserts it stays that way by activating Istanbul and
checking the rendering does not move.

**A defaulted period starts at the current instant.** `Beam.effective_from` defaults to
`timezone.now()`. S11 recorded this as a sharp edge awaiting the answer: an allocation entered
through a `datetime-local` field is truncated to the minute, so a Beam created seconds earlier
could reject a Path starting "now". Rounding back to midnight would have made that edge
disappear — and would have let a Beam created at 09:00 accept allocations from nine hours before
it existed. The answer confirms the instant, so the edge stays and the flag comes off.

## Consequences

`OQ-23` is closed, and the `SystemSetting` for a display time zone listed in `docs/design/02` §8
is not built. Adding one later means adding a *secondary* display, which is a template change
and not a settings change.

Timestamps in a golden example must carry a zone. The scenario harness refuses a naive value
rather than assuming one, because a naive string in a file exchanged with RF engineering would
be read differently by its author and by the platform — which is the same failure this ADR is
about, one layer out.
