"""Taking a backup, and describing it well enough to trust later. §22.4.

**A dump on its own is not a backup.** It is a file that might restore, and the only way to
find out is to try — which is what `operations.drill` is for. What this module adds is the
part that makes the attempt meaningful: a **manifest** written beside every dump recording
what the database contained at the moment it was taken.

Without one, a restore drill can only say "the archive loaded". With one it can say the
schema is at the migration the manifest names, and the restored `satnet_path` table holds the
number of rows the source held. A dump that restores cleanly and is missing half its rows is
the failure mode a drill exists to catch, and row counts are the cheapest way to catch it.

**The custom format, not plain SQL.** ``pg_dump -Fc`` is compressed, is restorable in parallel,
and — the reason that matters here — ``pg_restore`` will refuse an archive whose header is
damaged instead of executing the half of it that parsed. A SQL dump piped into ``psql`` runs
until it hits the corruption, leaving a database that is neither the old one nor the new one.

**Nothing here writes to the database.** ``pg_dump`` opens its own connection; this module
shells out to it rather than reimplementing it, because a hand-rolled dumper is a second
opinion about what the schema is, and the one thing a backup must not have is an opinion.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection

from operations.constants import COUNTED_TABLES, MANIFEST_SUFFIX, MANIFEST_VERSION

#: How long a dump or a restore may run before it is treated as hung. Generous, because a
#: dump of a real plan is minutes rather than seconds, and bounded, because a backup job that
#: never returns is an outage nobody is paged for.
TIMEOUT_SECONDS = 60 * 60


class BackupError(RuntimeError):
    """A dump or restore did not complete, with what the tool said."""


@dataclasses.dataclass(frozen=True)
class Manifest:
    """What the database held when the dump was taken. §22.4."""

    version: int
    taken_at: str
    database: str
    postgres_version: str
    migration_head: dict[str, str]
    row_counts: dict[str, int]
    dump_sha256: str
    dump_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Manifest:
        known = {field.name for field in dataclasses.fields(cls)}
        # Unknown keys are dropped rather than fatal: a manifest is read by a *later* version
        # of this platform than the one that wrote it, which is the normal case for a backup.
        return cls(**{key: value for key, value in payload.items() if key in known})


@dataclasses.dataclass(frozen=True)
class Backup:
    """A completed dump and the manifest beside it."""

    dump_path: Path
    manifest_path: Path
    manifest: Manifest


def take(destination: Path, *, label: str = "") -> Backup:
    """Dump the configured database and write a manifest beside it. §22.4."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"vsat-{stamp}{f'-{label}' if label else ''}.dump"
    dump_path = destination / name

    # Counts are read *before* the dump so that a database changing under a long dump cannot
    # produce a manifest that describes a state the archive never held. pg_dump takes its own
    # consistent snapshot; this is the closest honest approximation of the same instant, and
    # the drill's comparison is "at least as many", not "exactly", for that reason.
    counts = row_counts()
    _run(_dump_command(dump_path), what="pg_dump")

    if not dump_path.exists() or dump_path.stat().st_size == 0:
        raise BackupError(f"pg_dump reported success but wrote nothing to {dump_path}.")

    manifest = Manifest(
        version=MANIFEST_VERSION,
        taken_at=datetime.datetime.now(datetime.UTC).isoformat(),
        database=_settings()["NAME"],
        postgres_version=_postgres_version(),
        migration_head=migration_head(),
        row_counts=counts,
        dump_sha256=digest(dump_path),
        dump_bytes=dump_path.stat().st_size,
    )
    manifest_path = dump_path.with_suffix(dump_path.suffix + MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n")

    return Backup(dump_path=dump_path, manifest_path=manifest_path, manifest=manifest)


def read_manifest(dump_path: Path) -> Manifest:
    """The manifest beside a dump, or a clear refusal.

    A dump with no manifest is not restorable *with confidence*, which is a different thing
    from not restorable. The message says so rather than pretending the file is unusable.
    """
    path = manifest_path_for(dump_path)
    if not path.exists():
        raise BackupError(
            f"{path.name} is missing, so there is nothing to verify this dump against. "
            f"pg_restore may still load it, but the drill cannot confirm it is complete."
        )
    return Manifest.from_dict(json.loads(path.read_text()))


def manifest_path_for(dump_path: Path) -> Path:
    return Path(str(dump_path) + MANIFEST_SUFFIX)


def verify_digest(dump_path: Path, manifest: Manifest) -> None:
    """Refuse an archive whose bytes are not the bytes that were dumped. §22.4.

    Checked before `pg_restore` is asked to open it, because a truncated transfer is the most
    common way a backup fails and finding out from a restore error halfway through is finding
    out too late.
    """
    actual = digest(dump_path)
    if actual != manifest.dump_sha256:
        raise BackupError(
            f"{dump_path.name} does not match its manifest: SHA-256 {actual[:12]}… against "
            f"{manifest.dump_sha256[:12]}…. The archive was altered or truncated in transit."
        )


def restore(dump_path: Path, *, into: str) -> None:
    """Load a dump into a **named** target database. §22.4.

    ``into`` has no default and never falls back to the configured database. A restore is the
    one operation in this product that destroys data by design, and a command that could do it
    to production by omission is a command somebody will eventually run by omission.
    """
    if not into:
        raise BackupError("A restore needs a target database named explicitly.")
    if into == _settings()["NAME"]:
        raise BackupError(
            f"{into!r} is the database this process is connected to. Restore into a scratch "
            f"database and verify there — a drill that overwrites the source proves nothing "
            f"and destroys the thing it was checking."
        )

    _run(_createdb_command(into), what="createdb", allow_failure=True)
    _run(_restore_command(dump_path, into), what="pg_restore")


def row_counts(tables: tuple[str, ...] = COUNTED_TABLES) -> dict[str, int]:
    """How many rows each counted table holds, skipping tables that do not exist.

    Missing tables are skipped rather than fatal because this same function runs against a
    freshly restored database whose migration state may predate a table in the list — which is
    a finding for the drill to report, not a crash for this function to raise.
    """
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in tables:
            if table not in connection.introspection.table_names(cursor):
                continue
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 - a fixed list
            counts[table] = int(cursor.fetchone()[0])
    return counts


def migration_head() -> dict[str, str]:
    """The last applied migration per app — the schema's version, in the schema's own words."""
    from django.db.migrations.recorder import MigrationRecorder

    head: dict[str, str] = {}
    for record in MigrationRecorder.Migration.objects.order_by("app", "id"):
        head[record.app] = record.name
    return head


def digest(path: Path) -> str:
    """SHA-256 of a file, read in blocks so a large dump does not become a large allocation."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _settings() -> dict[str, Any]:
    return settings.DATABASES["default"]


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise BackupError(
            f"{name} is not on PATH. The PostgreSQL client tools are what take and load a "
            f"dump; the application image installs them and a host running the runbook needs "
            f"them too."
        )
    return found


def _connection_arguments() -> list[str]:
    """Host, port and user, taken from Django's own configuration.

    Read from settings rather than from the environment so that a backup is taken from the
    database the application is actually using. A backup job configured separately is a backup
    job that eventually points at the wrong host and reports success for years.
    """
    configured = _settings()
    return [
        "--host",
        str(configured.get("HOST") or "localhost"),
        "--port",
        str(configured.get("PORT") or 5432),
        "--username",
        str(configured.get("USER") or ""),
    ]


def _environment() -> dict[str, str]:
    """The subprocess environment, with the password passed the way libpq expects.

    ``PGPASSWORD`` rather than a command-line argument: arguments are visible in ``ps`` to
    every user on the host, and a database password in a process listing is a credential
    disclosed to anybody who can run ``ps``.
    """
    environment = dict(os.environ)
    password = _settings().get("PASSWORD")
    if password:
        environment["PGPASSWORD"] = str(password)
    return environment


def _dump_command(dump_path: Path) -> list[str]:
    return [
        _tool("pg_dump"),
        *_connection_arguments(),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(dump_path),
        str(_settings()["NAME"]),
    ]


def _createdb_command(into: str) -> list[str]:
    return [_tool("createdb"), *_connection_arguments(), into]


def _restore_command(dump_path: Path, into: str) -> list[str]:
    return [
        _tool("pg_restore"),
        *_connection_arguments(),
        "--dbname",
        into,
        # A restore into a scratch database meets objects it does not own and extensions that
        # already exist. Neither is a reason to stop, and `--exit-on-error` off with the errors
        # reported is the posture that lets a drill see the whole picture rather than the first
        # complaint.
        "--no-owner",
        "--no-privileges",
        str(dump_path),
    ]


def _postgres_version() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        return str(cursor.fetchone()[0])


def _run(command: list[str], *, what: str, allow_failure: bool = False) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - a fixed argument list, never a shell
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{what} did not finish within {TIMEOUT_SECONDS} seconds.") from exc
    except OSError as exc:
        raise BackupError(f"{what} could not be started: {exc}") from exc

    if completed.returncode != 0 and not allow_failure:
        raise BackupError(f"{what} failed ({completed.returncode}): {completed.stderr.strip()}")
    return completed.stdout
