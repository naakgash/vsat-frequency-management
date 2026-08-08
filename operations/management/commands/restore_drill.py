"""Check that the database this process is pointed at is a usable restore. §22.4.

Runs against **whatever database is configured**, and writes nothing — the whole run is one
transaction that is rolled back. Point a process at the restored scratch database and run this;
``verify_restore`` is the command that does the pointing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from audit import services as audit_services
from operations import backup, drill
from operations.constants import RESTORE_DRILL_FAILED, RESTORE_DRILL_PASSED


class Command(BaseCommand):
    help = "Verify a restored database is usable (specification section 22.4)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--manifest",
            default="",
            help="The manifest of the dump this database was restored from.",
        )
        parser.add_argument("--as", dest="username", default="", help="Sign in as this user.")
        parser.add_argument("--password", default="", help="That user's password.")
        parser.add_argument(
            "--json", action="store_true", help="Emit the report as JSON for a monitor to read."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        manifest = None
        if options["manifest"]:
            path = Path(options["manifest"])
            if not path.exists():
                raise CommandError(f"{path} does not exist.")
            manifest = backup.Manifest.from_dict(json.loads(path.read_text()))

        report = drill.run(
            manifest=manifest,
            username=options["username"],
            password=options["password"],
        )

        if options["json"]:
            self.stdout.write(json.dumps(report.as_dict(), indent=2))
        else:
            for check in report.checks:
                style = self.style.SUCCESS if check.ok else self.style.ERROR
                self.stdout.write(style(str(check)))

        # Recorded in the drill's own database, which is the restored one — so the record of
        # the drill lands beside the data it was checking rather than in production.
        audit_services.record(
            action=RESTORE_DRILL_PASSED if report.ok else RESTORE_DRILL_FAILED,
            outcome="SUCCESS" if report.ok else "FAILURE",
            after=report.as_dict(),
            message=(
                "Restore drill passed"
                if report.ok
                else f"Restore drill failed: {len(report.failures)} check(s)"
            ),
        )

        if not report.ok:
            raise CommandError(
                f"{len(report.failures)} check(s) failed. This archive is not a verified "
                f"restore — see docs/runbooks/restore.md."
            )
        self.stdout.write(self.style.SUCCESS("Restore verified."))
