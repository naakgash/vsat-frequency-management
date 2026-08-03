"""Readiness and liveness checks.

Specification section 21 requires /health/live and /health/ready. The distinction is
operational, not cosmetic:

* **live**  — the process is running and can serve a request. It must not touch the
  database, or a database blip would cause the orchestrator to kill healthy workers.
* **ready** — the process can actually do useful work: the database answers, and the
  extensions the schema depends on are installed.

The extension check exists because the overlap protection of specification section 8.3
is built on ``btree_gist``. A database missing it would accept every migration that does
not need it and then silently fail to enforce anything at all. That must be loud.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from django.db import DatabaseError, connection

# Extensions the schema depends on. See docs/design/04, section 1.
REQUIRED_EXTENSIONS = ("btree_gist", "citext", "pgcrypto")

CheckStatus = Literal["pass", "fail"]


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of a single readiness check."""

    name: str
    status: CheckStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def check_database() -> CheckResult:
    """Verify the database answers a trivial query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        return CheckResult("database", "fail", _redact(exc))
    return CheckResult("database", "pass")


def check_extensions() -> CheckResult:
    """Verify every required PostgreSQL extension is installed."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = ANY(%s)",
                [list(REQUIRED_EXTENSIONS)],
            )
            installed = {row[0] for row in cursor.fetchall()}
    except DatabaseError as exc:
        return CheckResult("extensions", "fail", _redact(exc))

    missing = sorted(set(REQUIRED_EXTENSIONS) - installed)
    if missing:
        return CheckResult("extensions", "fail", f"missing: {', '.join(missing)}")
    return CheckResult("extensions", "pass")


def run_readiness_checks() -> list[CheckResult]:
    """Run every readiness check.

    The database check runs first; if it fails, the extension check would only repeat
    the same connection error with less useful wording.
    """
    database = check_database()
    if not database.ok:
        return [database, CheckResult("extensions", "fail", "database unavailable")]
    return [database, check_extensions()]


def _redact(exc: DatabaseError) -> str:
    """Reduce a database error to a safe category.

    Health endpoints are unauthenticated (they are polled by the orchestrator before
    any session exists), so the response must not disclose hostnames, usernames or
    connection strings — specification section 21.15.
    """
    return type(exc).__name__
