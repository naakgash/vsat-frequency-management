# ADR-0011 — Specification Dictionary as the single source of field wording

**Status:** Accepted
**Date:** 2026-08-03
**Slice:** S3 — Specification Dictionary and the Info Popover
**Specification:** §2, §9.4, §10.3, §26.2, §26.3

## Context

The product displays dense engineering data. A Satnet Path table shows RF edges on both
sides of the payload, L-band IF, symbol rate, roll-off, occupied and allocated bandwidth,
and guard values — in codes such as `FWD_HUB_UL_START_RF`, because §10.3 requires codes as
the compact primary representation. Codes are unreadable without an explanation to hand.

§2 requires that explanation to be admin-managed data, and states two rules that pull in
opposite directions:

- the internal code *"must not be freely renamed after it is used by application logic"*;
- an administrator may edit *"its human-readable name, description, help text, unit
  presentation, visibility, and display order without changing calculation semantics."*

And one prohibition: *"Do not hard-code the same specification description independently
in multiple templates."*

## Decision

One `SpecificationDefinition` row per technical field, split along the line §2 draws:

| Semantic — fixed in code | Presentation — admin-editable |
|---|---|
| `code`, `data_type`, `category`, `direction_applicability`, `is_calculated` | `display_name`, `short_name`, `description`, `help_text`, `unit`, `display_precision`, `calculation_note`, `source_reference`, visibility flags, `display_order` |

Rendering goes through **one** component: the `{% spec_code %}` / `{% spec_label %}` tags
and the `partials/spec_info_button.html` partial. `{% spec_value %}` formats numbers using
the dictionary's unit and precision, so changing how a frequency is displayed is an
administrator's edit rather than a code change (§9.4).

Code immutability is enforced at three depths, because the consequence of a rename is
silent:

1. the form omits `code` from its fields entirely, so a crafted POST cannot carry it;
2. the service rejects any change outside an explicit editable-field list;
3. a database trigger refuses to rename a row marked `is_system_managed`.

## Consequences

**What this buys.**

An administrator corrects a description once and every screen that renders the code picks
it up — which is the whole point of §2, and the reason a guard-rail test fails the build
if seeded wording appears in a template.

Display precision is data. When RF engineering decides that kHz resolution is not enough,
that is a field edit, not a release.

The dictionary doubles as a register of unanswered questions. A specification with no
description is an unresolved engineering item, surfaced on the dictionary screen and by
`manage.py check_specifications`, which keeps §26.20 visible instead of letting a blank
help text ship unnoticed.

**What it costs.**

Every rendered code is a dictionary lookup. Mitigated by a request-scoped cache and a
`{% prime_specs %}` tag that loads a whole table's codes in one query; a dense table costs
one query rather than one per column. The cache is deliberately request-scoped, not
process-scoped: a process cache would leave other workers serving stale descriptions until
they were recycled.

A missing dictionary row degrades to a bare code with no information button rather than
raising. That is the right failure mode for a rendering component, but it means a missing
row is quiet — hence the registry-consistency test and the management command.

**What was deliberately not built.** No per-user or per-locale wording: §1 fixes the
product as English, and a second wording axis would reintroduce exactly the divergence
this decision removes.

## Alternatives considered

**Descriptions as Python constants** — rejected. §2 requires admin management, and a
description change would become a code change and a deploy.

**Django's `help_text` on each model field** — rejected. It cannot carry unit, precision,
calculation note, source reference or visibility; it is not admin-editable; and the same
specification appears on several models, which would mean several copies — the precise
duplication §2 forbids.

**Bootstrap popovers for the information button** — rejected. Bootstrap builds popover
content from a data attribute at show time, which keeps the description out of the
accessibility tree until the user opens it, and its default triggers lean on hover. §2
requires the control to be usable without relying on hover, so the component is
hand-written: a real `<button>`, correct `aria-expanded` and `aria-controls`, content
present in the DOM whether or not it is visible, Escape to dismiss with focus returned to
the trigger. Eight browser-driven tests exercise those behaviours.
