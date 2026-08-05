# Golden worked examples

**This directory is deliberately empty. OQ-22.**

Specification §24 asks for validated worked examples: a real symbol rate, a real roll-off, a
real Payload Path and a real Equipment Profile, with the numbers RF engineering expects the
platform to produce at every step.

They are the only test that can tell us the engine agrees with the people who own the
domain. Everything in `tests/domain/` proves the engine is *self-consistent* — that widths
survive translation, that rounding goes outward, that a round trip returns what it started
with. None of it proves the answers are the ones RF engineering would give.

## What the answer asks for

RF engineering's answer to OQ-22 (2026-08-05, recorded as **A-29**) is more specific than the
question was, in two ways.

**Provenance.** The example comes from a *currently operational* HTS Forward Satnet Path whose
frequency plan, Beam assignment, hub LO and IF limits can be verified against existing
engineering data:

> A hypothetical software-defined payload example or data generated from the implementation
> itself is not sufficient.

**Scope.** It states the Payload Path Window, the Beam Spectrum Assignment, the RF/IF conversion
rule, the equipment limits, the validity periods, the requested allocation and the expected
free-capacity result — and three outcomes that have nothing to do with arithmetic:

| Scenario kind | What it proves |
|---|---|
| `REJECT_SHARED_PAYLOAD_INPUT` | An overlapping allocation through **another Hub, Beam or redundant ground site**, on the same payload input and polarization, is refused. |
| `ACCEPT_INDEPENDENT_POLARIZATION` | An allocation on a polarization whose RF chains are independently implemented *may* be accepted. |
| `REJECT_OUTSIDE_ASSIGNMENT` | An allocation outside the Beam Spectrum Assignment is refused. |
| `REJECT_OUTSIDE_VALIDITY` | An allocation outside that assignment's validity period is refused. |

Those are the **OQ-25** reuse cases (**A-21**), which is presumably why they are here: the golden
example is meant to prove the reuse model end to end, not only the bandwidth arithmetic.

## Why nothing is here yet

Writing a plausible example would be worse than having none. It would go green, it would
look like validation, and it would be checking the engine against itself with extra steps.
§26.20 forbids exactly this — and the answer says it directly: an example produced by the
implementation proves nothing about the implementation.

## Adding one

Start from `docs/rf-confirmation/templates/golden-example.template.json`, which carries the
shape and no values. Drop the filled-in file in this directory; both harnesses discover it.

* `../test_golden_examples.py` — the arithmetic: bandwidth, placement, translation, RF/IF. Its
  docstring holds the annotated format.
* `../test_golden_scenarios.py` — the platform: it builds the master data the file describes,
  allocates what it asks for, checks the free capacity, and runs the four scenarios through the
  real services.

Two fields are not optional. `source` — who supplied the numbers, and when; an example whose
provenance nobody can state is not a golden example. And every timestamp carries a zone: values
are UTC and say so (**A-28**), because a naive string is read differently by its author and by
the platform.

The example's periods must be **in force now**. The platform resolves entitlements and
reservations as at the current instant, so a forward-dated or expired example cannot be run end
to end — the harness says so rather than working around it, since working around it would mean
rewriting an engineer's dates. The answer asks for a currently operational Path anyway.

## The gate

`test_golden_examples.py` skips while this directory is empty, and **fails** when
`VSAT_REQUIRE_GOLDEN_EXAMPLES=1` is set. Phase 9 sets it. That is what stops "we will add
them later" from quietly becoming "we shipped without them".

OQ-22 closes only when the expected results *"have been calculated independently by an RF
engineer, recorded in the golden-example file and matched exactly by the engine"*. Two of those
three are outside this repository.
