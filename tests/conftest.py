"""Shared test fixtures and helpers.

Tests run against a real PostgreSQL cluster. See config/settings/test.py for why there
is no SQLite path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that are never part of the product surface: third-party code, build
# output, virtual environments and version-control metadata.
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    "__pycache__",
    "staticfiles",
    "node_modules",
    "static/vendor",
}


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT).as_posix()
    if any(relative.startswith(f"{prefix}/") or relative == prefix for prefix in EXCLUDED_DIRS):
        return True
    return any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts)


def tracked_files(*suffixes: str) -> list[Path]:
    """Return in-repository source files with any of the given suffixes.

    Driven by git rather than a directory walk so that build output, virtual
    environments and other ignored artefacts can never influence a guard-rail test.

    ``--others --exclude-standard`` includes files that are new but not yet staged. That
    matters: a guard rail that only inspects committed files would let a template
    containing a forbidden term pass locally and fail only after it was committed, which
    is precisely when it is most annoying to fix.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for name in result.stdout.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if not path.is_file() or _is_excluded(path):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        files.append(path)
    return files


@pytest.fixture
def anonymous_client(client):
    """Django test client with no authenticated session."""
    return client
