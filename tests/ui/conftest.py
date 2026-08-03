"""Browser test configuration.

Playwright normally downloads a browser build matching its own version. This project
does not: specification section 22.2 targets an on-premises deployment that may have no
outbound internet access, and CI should not fetch a browser on every run either.

So the browser is located rather than downloaded. When none is present the browser tests
skip, which keeps the rest of the suite runnable anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Playwright's synchronous API runs a greenlet-backed event loop in the test thread.
# Django detects the running loop and refuses ORM access with SynchronousOnlyOperation,
# even though the calls are on the test's own thread and are genuinely safe here. This is
# the documented escape hatch.
#
# It has to be set here, in the conftest body, rather than in a fixture: the session-scoped
# fixtures that browser tests depend on (live_server among them) touch the ORM during their
# own setup, before any function-scoped fixture could put the variable in place.
#
# The cost is that it is set for the whole pytest process, not only for browser tests, and
# therefore inherited by any subprocess a test starts. `manage.py check --deploy` reports it
# as async.E001, correctly — it is a genuine deployment error and a test-harness concession
# at the same time. Anything spawning a Django process from a test must drop it; see
# tests/test_container_startup.py, which is where that bit.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

BROWSER_ROOT = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))


def find_chromium() -> str | None:
    """Return a usable Chromium executable, or None.

    Globbed rather than pinned to a build number: Playwright's expected build and the
    installed build routinely differ, and a mismatch is not a reason to lose the only
    tests that can prove keyboard accessibility.
    """
    candidates = [
        *sorted(BROWSER_ROOT.glob("chromium-*/chrome-linux/chrome")),
        *sorted(BROWSER_ROOT.glob("chromium_headless_shell-*/chrome-linux/headless_shell")),
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


CHROMIUM = find_chromium()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Point Playwright at the installed browser instead of a downloaded one."""
    if CHROMIUM is None:
        pytest.skip("No Chromium build found; browser tests skipped.")
    return {
        **browser_type_launch_args,
        "executable_path": CHROMIUM,
        # Chromium's sandbox needs kernel capabilities a container usually does not
        # grant, and without these the launch hangs rather than failing cleanly. The
        # browser only ever loads pages from the test server.
        "args": [
            *browser_type_launch_args.get("args", []),
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }


def pytest_collection_modifyitems(config, items):
    """Skip browser-marked tests when no browser is available."""
    if CHROMIUM is not None:
        return
    skip = pytest.mark.skip(reason="No Chromium build found; browser tests skipped.")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)
