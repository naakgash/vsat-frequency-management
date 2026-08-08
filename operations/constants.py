"""Operations audit actions and the backup manifest's shape. §21, §22.4."""

from __future__ import annotations

BACKUP_TAKEN = "BACKUP_TAKEN"
BACKUP_FAILED = "BACKUP_FAILED"
RESTORE_DRILL_PASSED = "RESTORE_DRILL_PASSED"
RESTORE_DRILL_FAILED = "RESTORE_DRILL_FAILED"

#: The manifest that travels beside a dump. Its version is in the file, because a manifest
#: read by a newer platform than the one that wrote it is the normal case during a restore —
#: the whole point of a backup is that it is opened later.
MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"

#: Tables whose row counts are recorded and re-checked after a restore. Not every table: this
#: is the list somebody would count by hand to decide whether a restore worked, and a hundred
#: numbers nobody reads is the same as none.
#:
#: `audit_event` is here for a reason beyond its size. It is append-only and monotonic, so a
#: restored copy holding *fewer* rows than the manifest is the clearest possible signal that
#: the archive is not what it claims to be.
COUNTED_TABLES: tuple[str, ...] = (
    "satnet_path",
    "spectrum_reservation",
    "satnet",
    "beam",
    "beam_spectrum_assignment",
    "frequency_window",
    "payload_path",
    "spectrum_resource",
    "approval_decision",
    "import_batch",
    "import_row",
    "audit_event",
    "specification_definition",
    "accounts_user",
)
