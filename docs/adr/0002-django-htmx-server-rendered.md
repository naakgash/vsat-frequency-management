# ADR-0002 — Server-rendered Django with HTMX

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S1 — Foundation
**Specification:** §9, §11, §19.1, §19.2, §19.4

## Context

The operator workflow is highly interactive. As the operator adjusts requested bandwidth,
roll-off, guard policy and centre frequency, the interface must continuously show symbol
rate, occupied and allocated bandwidth, RF edges on both sides of the payload, L-band IF,
distances to Frequency Window edges and to neighbouring allocations, and the validation
state of both RF legs (§9.4).

Two constraints shape how that interactivity may be built:

- §11 requires a **single calculation engine** and forbids formulas being placed
  independently in templates, forms, imports or JavaScript. Client-side calculation is
  permitted for preview, but the backend result is authoritative.
- §19.4 rules out a separate SPA frontend and CDN-only dependencies without proven need.

## Decision

Server-rendered Django templates, with **HTMX** for partial updates. Each interactive
step of the wizard posts its current state to a fragment endpoint, which runs the real
calculation engine and returns rendered HTML.

Vendored assets only: Bootstrap 5 and HTMX are committed under `static/vendor/`, and a
test (`tests/ui/test_no_external_assets.py`) fails the build if any template references
an external host.

## Consequences

**What this buys.**

The live preview is computed by the same code that validates the save. This is the
decisive argument: a JavaScript reimplementation of bandwidth, translation and IF
arithmetic would be a *second* calculation engine, which §11 exists to prevent. A
preview that disagrees with the server is worse than no preview — it teaches operators to
trust a number that will be rejected.

The on-premises deployment may have no outbound internet access. Vendored assets mean the
application renders correctly on an isolated network.

Server-side rendering keeps authorization in one place. There is no client-side state
holding data the user is not entitled to (docs/design/03 §4).

**What it costs.**

Every keystroke-driven preview update is a round trip. On a LAN-deployed on-premises
application this is acceptable; a debounce on the input handlers keeps request volume
sane. If a specific interaction is ever proven too slow, the escape hatch is a
client-side *display* helper — never a client-side recalculation.

Rich spectrum visualisation still needs real JavaScript. ECharts arrives with the
spectrum view in S9, vendored on the same terms. It renders data the server computed; it
does not compute.

**Where the boundary sits.**

Permitted in JavaScript: display formatting, unit presentation, debouncing, chart
rendering, keyboard interaction, CSRF wiring.
Not permitted in JavaScript: symbol rate, occupied or allocated bandwidth, RF edges,
translation, IF conversion, guard resolution, gap analysis, conflict detection.

## Alternatives considered

**Separate SPA (React/Vue) with a JSON API** — rejected. §19.4 rules it out without
proven need, and it makes the two-engine problem the default rather than the exception.

**Plain Django with full-page reloads** — rejected. §9.4 requires a live preview; a
full-page reload per field change would make the guided workflow unusable.

**Django + Alpine.js for local state** — not adopted now. HTMX covers the fragment-swap
need. Alpine may be revisited for purely presentational state (collapsing panels), which
would not involve calculation.
