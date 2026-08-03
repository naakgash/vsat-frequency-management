"""No CDN-only dependencies.

Specification section 19.4 rules out CDN-only dependencies, and section 22.2 targets an
on-premises deployment that may have no outbound internet access at all. A template
referencing an external host would render an unstyled, non-functional page there.
"""

from __future__ import annotations

import re

from tests.conftest import REPO_ROOT, tracked_files

# src="https://..." / href='http://...' — attribute references to an external origin.
EXTERNAL_ASSET_REFERENCE = re.compile(
    r"""(?:src|href)\s*=\s*["'](?:https?:)?//""",
    re.IGNORECASE,
)


def test_templates_reference_no_external_hosts():
    offenders = []

    for path in tracked_files(".html"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if EXTERNAL_ASSET_REFERENCE.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "Templates must load assets from static/, not from an external host "
        "(specification section 19.4).\n" + "\n".join(offenders)
    )


def test_vendored_assets_are_present():
    """The vendored files the base template loads must actually exist.

    Without this, a missing vendor file only shows up as an unstyled page in a browser.
    """
    expected = [
        REPO_ROOT / "static/vendor/bootstrap/bootstrap.min.css",
        REPO_ROOT / "static/vendor/bootstrap/bootstrap.bundle.min.js",
        REPO_ROOT / "static/vendor/htmx/htmx.min.js",
    ]

    missing = [path.name for path in expected if not path.is_file()]

    assert not missing, f"vendored assets missing: {missing}. Run `make vendor`."
