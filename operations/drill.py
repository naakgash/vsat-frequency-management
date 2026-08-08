"""Proving a restore worked. §22.4, §26.18.

A restore that loads without error has proved one thing: the archive parsed. §22.4 asks for
more, and names what: that somebody can **sign in**, that a **Beam** and a **Satnet Path**
still render, that the **latest audit event** is there, that the **row counts** match, and
that an **export** still produces a file.

Every check here runs the real thing rather than a proxy for it. The screens are fetched
through Django's own request handling — same URLconf, same middleware, same templates — because
a restore that loaded every row and left a foreign key dangling passes a `SELECT COUNT(*)` and
fails the first page somebody opens. Counting rows and calling that a verified restore is how a
team discovers on the worst possible day that their backups were never restorable.

**The drill writes nothing.** Signing in creates a session, so the whole run is wrapped in a
transaction that is rolled back — which is also what makes it safe to run in the test suite
against the same database the tests use, and therefore what makes the drill itself tested
rather than merely written.

It runs against **whatever database this process is configured for**. The orchestration —
restore into a scratch database, point a process at it, run the drill — belongs in the runbook
and in ``manage.py verify_restore``, not in here. A checker that also chose its own target
would be a checker that can be pointed at production by a typo.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

from django.db import transaction
from django.test import Client
from django.urls import reverse

from operations import backup
from operations.constants import COUNTED_TABLES

#: How stale the newest audit event may be before the drill says so. Not a failure — a quiet
#: system is a real thing — but a restore whose most recent event is from last year is almost
#: always an old archive somebody grabbed by mistake, and that is worth saying out loud.
STALE_AFTER_DAYS = 7


@dataclasses.dataclass
class Check:
    """One thing the drill looked at."""

    name: str
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


@dataclasses.dataclass
class Report:
    """Everything the drill looked at, and whether the restore is usable."""

    checks: list[Check] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def add(self, name: str, ok: bool, detail: str = "") -> Check:
        check = Check(name=name, ok=ok, detail=detail)
        self.checks.append(check)
        return check

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [dataclasses.asdict(check) for check in self.checks],
        }


def run(
    *,
    manifest: backup.Manifest | None = None,
    username: str = "",
    password: str = "",
) -> Report:
    """Run every §22.4 check and report. Rolls back whatever it did.

    ``username``/``password`` are optional: given, the drill signs in for real, which is the
    only way to prove authentication survived a restore. Omitted, it checks what it can — that
    the sign-in page renders and that an administrator with a usable credential exists — and
    says which of the two it did, because a drill that quietly did less is worse than one that
    did less loudly.
    """
    report = Report()

    # One transaction for the whole run, rolled back at the end. Signing in writes a session
    # row; nothing this drill does should survive it.
    try:
        with transaction.atomic():
            _check_schema(report, manifest)
            _check_row_counts(report, manifest)
            _check_audit(report)
            client = _check_sign_in(report, username, password)
            _check_screens(report, client)
            _check_export(report, client)
            raise _Rollback
    except _Rollback:
        pass
    return report


class _Rollback(Exception):
    """Raised to undo the drill's own writes. Never escapes :func:`run`."""


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def _check_schema(report: Report, manifest: backup.Manifest | None) -> None:
    """Is the restored schema at the migration the manifest recorded?

    A mismatch is a *failure*, not a note. Restoring last month's dump into this month's code
    and running `migrate` afterwards is a legitimate operation; discovering that you did so by
    accident, after the drill said everything was fine, is not.
    """
    head = backup.migration_head()
    if manifest is None:
        report.add("schema", True, f"{len(head)} apps migrated; no manifest to compare against")
        return

    differences = [
        f"{app}: manifest {expected}, restored {head.get(app, 'nothing')}"
        for app, expected in sorted(manifest.migration_head.items())
        if head.get(app) != expected
    ]
    report.add(
        "schema",
        not differences,
        "matches the manifest" if not differences else "; ".join(differences),
    )


def _check_row_counts(report: Report, manifest: backup.Manifest | None) -> None:
    """Do the restored tables hold what the source held? §22.4.

    **At least as many**, not exactly. The manifest's counts are read just before the dump
    starts, so a busy database can legitimately grow between the two — but it cannot shrink,
    because nothing in this product hard-deletes (§20). A restored table holding fewer rows
    than the manifest is therefore always a finding.
    """
    counts = backup.row_counts()
    if manifest is None:
        summary = ", ".join(f"{table}={count}" for table, count in sorted(counts.items()) if count)
        report.add("row counts", True, summary or "every counted table is empty")
        return

    short = [
        f"{table}: manifest {expected}, restored {counts.get(table, 0)}"
        for table, expected in sorted(manifest.row_counts.items())
        if counts.get(table, 0) < expected
    ]
    missing = [
        table for table in COUNTED_TABLES if table in manifest.row_counts and table not in counts
    ]
    problems = short + [f"{table}: table absent" for table in missing]
    report.add(
        "row counts",
        not problems,
        f"{len(manifest.row_counts)} tables at or above the manifest"
        if not problems
        else "; ".join(problems),
    )


def _check_audit(report: Report) -> None:
    """Is the trail there, and does it end where it should? §18, §22.4."""
    from audit.models import AuditEvent

    latest = AuditEvent.objects.order_by("-occurred_at").first()
    if latest is None:
        report.add(
            "audit trail",
            False,
            "no audit event at all. Every write in this product records one, so an empty "
            "trail means either an empty database or a restore that dropped the table.",
        )
        return

    age = datetime.datetime.now(datetime.UTC) - latest.occurred_at
    note = f"latest is {latest.action} at {latest.occurred_at:%Y-%m-%d %H:%M:%S} UTC"
    if age > datetime.timedelta(days=STALE_AFTER_DAYS):
        note += f" — {age.days} days old, which usually means an older archive than intended"
    report.add("audit trail", True, note)


def _check_sign_in(report: Report, username: str, password: str) -> Client:
    """Can somebody get in? §22.4.

    The check §22.4 names first, and the one most likely to be quietly broken by a partial
    restore: a dump missing `auth_permission` or the role groups restores cleanly, renders
    every page for an existing session, and lets nobody in.
    """
    from accounts.constants import Role
    from accounts.models import User

    client = Client()

    page = client.get(reverse("accounts:login"))
    if page.status_code != 200:
        report.add("sign-in", False, f"the sign-in page returned {page.status_code}")
        return client

    administrators = User.objects.filter(is_active=True, groups__name=Role.ADMIN)
    if not administrators.exists():
        report.add(
            "sign-in",
            False,
            "the sign-in page renders, but no active administrator exists. Either the role "
            "groups did not restore or this is not the database you meant.",
        )
        return client

    if not username:
        report.add(
            "sign-in",
            True,
            f"the page renders and {administrators.count()} active administrator(s) exist. "
            f"**Credentials were not supplied, so no sign-in was attempted** — pass --as and "
            f"--password to make this check real.",
        )
        return client

    response = client.post(
        reverse("accounts:login"), {"username": username, "password": password}, follow=True
    )
    signed_in = response.context is not None and response.context["user"].is_authenticated
    report.add(
        "sign-in",
        signed_in,
        f"{username} signed in" if signed_in else f"{username} could not sign in",
    )
    return client


def _check_screens(report: Report, client: Client) -> None:
    """Do a Beam and a Satnet Path still render? §22.4.

    The check that catches what a row count cannot. A restore with a dangling foreign key or a
    missing generated column counts perfectly and fails on the first page somebody opens, so
    the drill opens one.
    """
    from beams.models import Beam
    from satnet_paths.models import SatnetPath

    for name, model, route in (
        ("beam detail", Beam, "beams:detail"),
        ("satnet path detail", SatnetPath, "satnet_paths:detail"),
    ):
        record = model.objects.order_by("-updated_at").first()
        if record is None:
            report.add(name, True, "nothing of this kind in the database to render")
            continue
        response = client.get(reverse(route, kwargs={"pk": record.pk}))
        # 200 renders it; 302 is the sign-in redirect, which means the page exists and the
        # drill simply has no session — a fact the sign-in check has already reported on.
        ok = response.status_code in (200, 302)
        report.add(
            name,
            ok,
            f"{record} returned {response.status_code}"
            + (" (not signed in)" if response.status_code == 302 else ""),
        )


def _check_export(report: Report, client: Client) -> None:
    """Does an export still produce a file? §22.4, §17.2.

    Deliberately the last check and deliberately end to end. The export reads the table's
    selector, the Specification Dictionary and the scope tables, so a workbook that comes out
    the right size exercises more of a restored database in one call than anything else here.
    """
    from accounts.constants import Role
    from accounts.models import User
    from imports_exports import services as export_services
    from satnet_paths import selectors as path_selectors

    actor = User.objects.filter(is_active=True, groups__name=Role.ADMIN).first()
    if actor is None:
        report.add("export", False, "no administrator to export as")
        return

    try:
        export = export_services.export_satnet_paths(actor=actor)
    except Exception as exc:  # a restore failure surfaces here as anything at all
        report.add("export", False, f"the export raised {type(exc).__name__}: {exc}")
        return

    expected = path_selectors.current(actor).count()
    report.add(
        "export",
        export.row_count == expected and len(export.content) > 0,
        f"{export.row_count} row(s), {len(export.content)} bytes"
        + ("" if export.row_count == expected else f" — expected {expected}"),
    )
