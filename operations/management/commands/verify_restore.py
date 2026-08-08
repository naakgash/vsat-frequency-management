"""The restore drill of §22.4, end to end: check, restore, verify.

Four steps, and the order is the design:

1. **Verify the archive's SHA-256** against its manifest, before ``pg_restore`` is asked to
   open it. A truncated transfer is the most common way a backup fails, and finding out from a
   restore error halfway through is finding out too late.
2. **Restore into a named scratch database.** Never the configured one — `backup.restore`
   refuses that outright, because a drill that overwrites its own source proves nothing and
   destroys the thing it was checking.
3. **Run the drill against the restored database**, in a subprocess whose ``POSTGRES_DB``
   points at it. A subprocess rather than a second connection alias for one reason: the drill
   fetches real pages through the real URLconf, and a router that sent *some* of those queries
   elsewhere would be testing a database that never existed.
4. **Report**, and fail loudly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from operations import backup


class Command(BaseCommand):
    help = "Restore a dump into a scratch database and verify it is usable (section 22.4)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dump", required=True, help="The .dump file to verify.")
        parser.add_argument(
            "--into",
            required=True,
            help="Scratch database to restore into. Must not be the configured database.",
        )
        parser.add_argument("--as", dest="username", default="", help="Sign in as this user.")
        parser.add_argument("--password", default="", help="That user's password.")
        parser.add_argument(
            "--skip-restore",
            action="store_true",
            help="The target already holds the restore; only run the checks.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dump_path = Path(options["dump"])
        if not dump_path.exists():
            raise CommandError(f"{dump_path} does not exist.")

        try:
            manifest = backup.read_manifest(dump_path)
            self.stdout.write(f"Manifest taken {manifest.taken_at} from {manifest.database}")

            backup.verify_digest(dump_path, manifest)
            self.stdout.write(self.style.SUCCESS("SHA-256 matches the manifest."))

            if not options["skip_restore"]:
                self.stdout.write(f"Restoring into {options['into']}…")
                backup.restore(dump_path, into=options["into"])
                self.stdout.write(self.style.SUCCESS("Restored."))
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Running the drill against {options['into']}…")
        completed = subprocess.run(  # noqa: S603 - a fixed argument list, never a shell
            [
                sys.executable,
                "manage.py",
                "restore_drill",
                "--manifest",
                str(backup.manifest_path_for(dump_path)),
                *(["--as", options["username"]] if options["username"] else []),
                *(["--password", options["password"]] if options["password"] else []),
            ],
            # The one thing this subprocess exists to change. Everything else — host, user,
            # password, settings module — is inherited, so the drill runs against the same
            # deployment it would in production, pointed at a different database.
            env={**os.environ, "POSTGRES_DB": options["into"]},
            check=False,
            text=True,
        )

        if completed.returncode != 0:
            raise CommandError(
                f"The drill failed against {options['into']}. This archive is not a verified "
                f"restore — see docs/runbooks/restore.md. The scratch database has been left "
                f"in place so it can be examined."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{dump_path.name} is a verified restore. Drop {options['into']} when done."
            )
        )
