"""Forbidden-terminology guard rails.

Specification sections 4 and 7, acceptance criteria 26.5 and 26.9:

* the product contains no ``Interference Domain`` entity, menu, filter, form or
  user-facing concept, and no renamed replacement for it;
* ``Satnet Path`` is the only term for a direction-specific allocation. ``Carrier`` is
  not an entity label anywhere in the model, the API, the URLs or the interface.

These are enforced on every commit rather than by review, because the failure mode is a
single template or migration reintroducing the term months later.
"""

from __future__ import annotations

import re

import pytest

from tests.conftest import REPO_ROOT, tracked_files

# The design documents and the ADR that records the decision must be able to name the
# forbidden terms in order to forbid them. Nothing else may.
ALLOWED_PREFIXES = (
    "docs/design/",
    "docs/adr/",
    "docs/slices/",
    "tests/ui/test_terminology.py",
    # One file, not the directory it sits in. The OQ-25 briefing has to name the object
    # §4 removed in order to explain why the Beam became the reuse key — that is the whole
    # question it puts to RF engineering. The rest of docs/rf-confirmation/, including the
    # generated intake sheets, stays in scope: an allow-list over the directory would let
    # the term reach a column heading and nothing would notice.
    "docs/rf-confirmation/oq-25-26-27-briefing.md",
)

FORBIDDEN_PATTERNS = {
    # \bcarrier\b also catches Carriers, carrier_id, CarrierForm via the word boundary,
    # while leaving unrelated words such as "carrierless" out of scope deliberately:
    # the concern is the entity label, not the English word in prose.
    "Carrier": re.compile(r"\bcarriers?\b", re.IGNORECASE),
    "Interference Domain": re.compile(r"\binterference[\s_-]*domains?\b", re.IGNORECASE),
}

SCANNED_SUFFIXES = (".py", ".html", ".txt", ".md", ".js", ".css", ".yaml", ".yml", ".toml", ".sql")


def _scannable_files():
    for path in tracked_files(*SCANNED_SUFFIXES):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(ALLOWED_PREFIXES):
            continue
        yield path, relative


@pytest.mark.parametrize("term", sorted(FORBIDDEN_PATTERNS))
def test_forbidden_term_is_absent_from_the_product(term):
    pattern = FORBIDDEN_PATTERNS[term]
    offenders = []

    for path, relative in _scannable_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        f"'{term}' is forbidden by the root specification.\n"
        + "\n".join(offenders)
        + "\n\nUse 'Satnet Path' for a direction-specific allocation. The allocation scope "
        "is derived from Beam, Frequency Window, leg, polarization and time; it is not a "
        "configurable domain object."
    )


def test_the_guard_rail_actually_scans_something():
    """A guard rail that silently matches no files protects nothing."""
    scanned = list(_scannable_files())

    assert len(scanned) > 10, "terminology scan found almost no files; check the exclusions"
