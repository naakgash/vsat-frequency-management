"""Take a backup and record that it was taken. §22.4, §18."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from audit import services as audit_services
from operations import backup
from operations.constants import BACKUP_FAILED, BACKUP_TAKEN


class Command(BaseCommand):
    help = "Dump the database and write a manifest beside it (specification section 22.4)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--to",
            default=getattr(settings, "BACKUP_DIRECTORY", "/var/backups/vsat"),
            help="Directory to write the dump and its manifest into.",
        )
        parser.add_argument(
            "--label",
            default="",
            help="A word appended to the filename, e.g. pre-migration.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        destination = Path(options["to"])
        try:
            result = backup.take(destination, label=options["label"])
        except backup.BackupError as exc:
            # Recorded before raising. A backup that did not happen is the event somebody
            # needs to find later, and it is the one an unrecorded failure hides (§18).
            audit_services.record(
                action=BACKUP_FAILED,
                outcome="FAILURE",
                after={"destination": str(destination), "error": str(exc)},
                message=f"Backup failed: {exc}",
            )
            raise CommandError(str(exc)) from exc

        audit_services.record(
            action=BACKUP_TAKEN,
            after={
                "file": result.dump_path.name,
                "bytes": result.manifest.dump_bytes,
                "sha256": result.manifest.dump_sha256,
                "database": result.manifest.database,
                "rows": result.manifest.row_counts,
            },
            message=f"Backed up {result.manifest.database} to {result.dump_path.name}",
        )

        self.stdout.write(self.style.SUCCESS(f"Wrote {result.dump_path}"))
        self.stdout.write(f"Manifest {result.manifest_path}")
        self.stdout.write(f"SHA-256  {result.manifest.dump_sha256}")
        self.stdout.write(
            "Verify it with: manage.py verify_restore --dump "
            f"{result.dump_path} --into vsat_restore_drill"
        )
        # Said every time, because a backup nobody has ever restored is a file, not a backup.
        self.stdout.write(
            self.style.WARNING(
                "A backup is not verified until it has been restored. docs/runbooks/restore.md"
            )
        )
