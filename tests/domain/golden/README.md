# Golden worked examples

**This directory is deliberately empty. OQ-22.**

Specification §24 asks for validated FWD and RTN worked examples: a real symbol rate, a
real roll-off, a real Payload Path and a real Equipment Profile, with the numbers RF
engineering expects the platform to produce at every step.

They are the only test that can tell us the engine agrees with the people who own the
domain. Everything in `tests/domain/` proves the engine is *self-consistent* — that widths
survive translation, that rounding goes outward, that a round trip returns what it started
with. None of it proves the answers are the ones RF engineering would give.

## Why nothing is here yet

Writing a plausible example would be worse than having none. It would go green, it would
look like validation, and it would be checking the engine against itself with extra steps.
§26.20 forbids exactly this.

## Adding one

Drop a JSON file in this directory. `test_golden_examples.py` discovers and runs every one.
The format is in that file's docstring, and one field is not optional: `source` — who
supplied the numbers, and when. An example whose provenance nobody can state is not a
golden example.

## The gate

`test_golden_examples.py` skips while this directory is empty, and **fails** when
`VSAT_REQUIRE_GOLDEN_EXAMPLES=1` is set. Phase 9 sets it. That is what stops "we will add
them later" from quietly becoming "we shipped without them".
