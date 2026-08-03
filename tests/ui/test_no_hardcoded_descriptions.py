"""Specification descriptions live in the dictionary, nowhere else.

Specification section 2: *"Do not hard-code the same specification description
independently in multiple templates."*

The failure this prevents is slow and quiet. A description gets copied into a template
because it was quicker than loading it; a year later an administrator corrects the
description in the dictionary and one screen keeps showing the old wording. Nobody
notices, because nothing broke.
"""

from __future__ import annotations

import re

from tests.conftest import REPO_ROOT, tracked_files

#: Files permitted to contain specification wording: the registry that seeds it, the
#: single rendering component, and the design documents.
ALLOWED_PREFIXES = (
    "specifications/registry.py",
    "templates/partials/spec_info_button.html",
    "docs/",
    "tests/ui/test_no_hardcoded_descriptions.py",
    "tests/specifications/",
)

#: Distinctive phrases from the seeded descriptions and calculation notes. If one of
#: these appears outside the allowed files, the wording has been duplicated.
SEEDED_PHRASES = [
    "Symbol Rate = Occupied Bandwidth",
    "Occupied BW = Symbol Rate",
    "Allocated BW = Allocated End",
    "Raised-cosine roll-off factor applied to the symbol rate",
    "Bandwidth occupied by the transmission itself",
    "Total bandwidth reserved in the spectrum pool",
    "Lower edge of the allocated range on the forward hub uplink",
    "Separation applied below the occupied bandwidth",
    "Separation applied above the occupied bandwidth",
]

SCANNED_SUFFIXES = (".html", ".py", ".js")


def _scannable_files():
    for path in tracked_files(*SCANNED_SUFFIXES):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(ALLOWED_PREFIXES):
            continue
        yield path, relative


def test_no_seeded_description_is_duplicated_outside_the_dictionary():
    offenders = []

    for path, relative in _scannable_files():
        content = path.read_text(encoding="utf-8")
        for phrase in SEEDED_PHRASES:
            if phrase in content:
                offenders.append(f"{relative}: contains {phrase!r}")

    assert not offenders, (
        "Specification wording is duplicated outside the dictionary "
        "(specification section 2).\n" + "\n".join(offenders) + "\n\n"
        "Render it with {% spec_code %} or {% spec_label %} instead, so an "
        "administrator's edit reaches every screen."
    )


def test_templates_render_specification_codes_through_the_component():
    """A template that prints a bare code without the tag has no information button.

    Detected by looking for a raw code in template text outside a ``spec_*`` tag call.
    Only the codes named in specification section 2 are checked, to keep the rule sharp.
    """
    codes = [
        "FWD_HUB_UL_START_RF",
        "FWD_HUB_UL_CENTER_RF",
        "FWD_HUB_UL_END_RF",
        "FWD_REMOTE_DL_CENTER_RF",
        "RTN_REMOTE_UL_CENTER_RF",
        "RTN_HUB_DL_CENTER_RF",
        "L_BAND_CENTER_IF",
        "SYMBOL_RATE",
        "OCCUPIED_BANDWIDTH",
        "ALLOCATED_BANDWIDTH",
    ]
    pattern = re.compile("|".join(codes))
    offenders = []

    for path, relative in _scannable_files():
        if path.suffix != ".html":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not pattern.search(line):
                continue
            if "spec_code" in line or "spec_label" in line or "spec_value" in line:
                continue
            offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "A specification code appears in a template without the information component "
        "(acceptance criterion 26.3).\n" + "\n".join(offenders)
    )


def test_the_sweep_actually_scans_templates():
    """A guard rail that inspects nothing protects nothing."""
    templates = [p for p, _ in _scannable_files() if p.suffix == ".html"]

    assert len(templates) >= 5, f"only {len(templates)} templates scanned"
