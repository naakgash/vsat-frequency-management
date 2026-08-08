"""Backup, and the drill that decides whether a backup is real. §22.4, §26.18.

A dump that has never been restored is a file, not a backup. §22.4 says so and names the checks
a restore drill has to pass, and the point of this file is that those checks are **executed**
rather than described: the drill fetches real pages through the real URLconf, signs in through
the real form, and runs the real export.

The drill is safe to run here because it rolls back everything it does, which is the same
property that makes it safe to point at a production replica. A drill that could only be tested
by trusting it would be exactly the wrong thing to have written.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from audit.models import AuditEvent
from operations import backup, drill
from operations.constants import BACKUP_TAKEN, RESTORE_DRILL_PASSED
from satnet_paths.constants import PathStatus
from tests.factories import TEST_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def populated(lifecycle_world, make_path):
    """A database with something in it — a Beam, a Satnet, an allocation and a trail."""
    make_path(PathStatus.DRAFT, code="BK-1")
    return lifecycle_world


def a_manifest(**overrides) -> backup.Manifest:
    defaults = {
        "version": 1,
        "taken_at": "2026-08-08T10:00:00+00:00",
        "database": "vsat_dev",
        "postgres_version": "16.4",
        "migration_head": backup.migration_head(),
        "row_counts": backup.row_counts(),
        "dump_sha256": "0" * 64,
        "dump_bytes": 1024,
    }
    return backup.Manifest(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# The manifest — what makes a restore checkable at all
# ---------------------------------------------------------------------------
def test_a_manifest_round_trips(populated):
    manifest = a_manifest()

    assert backup.Manifest.from_dict(manifest.as_dict()) == manifest


def test_a_manifest_written_by_a_newer_platform_still_reads(populated):
    """A manifest is read by a *later* version than the one that wrote it. That is the
    normal case for a backup, so an unknown key is dropped rather than fatal."""
    payload = {**a_manifest().as_dict(), "something_added_in_2027": True}

    assert backup.Manifest.from_dict(payload).database == "vsat_dev"


def test_row_counts_cover_the_tables_somebody_would_count_by_hand(populated):
    counts = backup.row_counts()

    assert counts["satnet_path"] >= 1
    assert counts["audit_event"] >= 1
    assert counts["beam"] >= 1


def test_row_counts_skip_a_table_that_does_not_exist(populated):
    """The same function runs against a restored database whose schema may be older."""
    counts = backup.row_counts(("satnet_path", "a_table_from_a_future_slice"))

    assert set(counts) == {"satnet_path"}


def test_the_migration_head_names_every_migrated_app(populated):
    head = backup.migration_head()

    assert head["audit"]
    assert head["satnet_paths"]


# ---------------------------------------------------------------------------
# The archive — checked before pg_restore is asked to open it
# ---------------------------------------------------------------------------
def test_a_matching_digest_passes(tmp_path: Path, populated):
    archive = tmp_path / "vsat.dump"
    archive.write_bytes(b"not really a dump, but the bytes are what is hashed")

    backup.verify_digest(archive, a_manifest(dump_sha256=backup.digest(archive)))


def test_an_altered_archive_is_refused_with_both_digests(tmp_path: Path, populated):
    """The most common way a backup fails is a truncated transfer, and a restore error
    halfway through is finding out too late."""
    archive = tmp_path / "vsat.dump"
    archive.write_bytes(b"original")
    manifest = a_manifest(dump_sha256=backup.digest(archive))
    archive.write_bytes(b"tampered")

    with pytest.raises(backup.BackupError) as caught:
        backup.verify_digest(archive, manifest)

    assert "truncated" in str(caught.value)


def test_a_dump_with_no_manifest_says_what_is_missing(tmp_path: Path, populated):
    archive = tmp_path / "vsat.dump"
    archive.write_bytes(b"x")

    with pytest.raises(backup.BackupError) as caught:
        backup.read_manifest(archive)

    assert "cannot confirm it is complete" in str(caught.value)


# ---------------------------------------------------------------------------
# Restore — the one operation that destroys data by design
# ---------------------------------------------------------------------------
def test_a_restore_refuses_the_database_it_is_connected_to(tmp_path: Path, populated):
    """A drill that overwrites its own source proves nothing and destroys what it checked."""
    from django.conf import settings

    with pytest.raises(backup.BackupError) as caught:
        backup.restore(tmp_path / "vsat.dump", into=settings.DATABASES["default"]["NAME"])

    assert "scratch database" in str(caught.value)


def test_a_restore_refuses_an_unnamed_target(tmp_path: Path, populated):
    with pytest.raises(backup.BackupError):
        backup.restore(tmp_path / "vsat.dump", into="")


# ---------------------------------------------------------------------------
# The drill — §22.4's checks, executed
# ---------------------------------------------------------------------------
def test_every_check_passes_against_a_healthy_database(populated):
    report = drill.run()

    assert report.ok, [str(check) for check in report.failures]
    assert {check.name for check in report.checks} == {
        "schema",
        "row counts",
        "audit trail",
        "sign-in",
        "beam detail",
        "satnet path detail",
        "export",
    }


def test_the_drill_writes_nothing(populated):
    """Signing in creates a session and the export records an audit event. Neither survives.

    This is what makes the drill safe to point at a restored copy of production — and it is
    also what makes it testable here at all, in the same database as everything else.
    """
    before_events = AuditEvent.objects.count()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_session")
        before_sessions = cursor.fetchone()[0]

    drill.run()

    assert AuditEvent.objects.count() == before_events
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_session")
        assert cursor.fetchone()[0] == before_sessions


def test_a_restore_missing_rows_fails_the_drill(populated):
    """The failure a drill exists to catch: the archive loaded and half the plan is gone."""
    manifest = a_manifest(row_counts={**backup.row_counts(), "satnet_path": 9_999})

    report = drill.run(manifest=manifest)

    assert not report.ok
    assert any(check.name == "row counts" for check in report.failures)
    assert "9999" in str(report.failures[0]) or "9,999" in str(report.failures[0])


def test_a_database_that_has_grown_since_the_dump_still_passes(populated, make_path):
    """At least as many, not exactly. Nothing in this product hard-deletes (§20), so a
    restored table can legitimately hold more than the manifest and never fewer."""
    manifest = a_manifest()
    make_path(PathStatus.DRAFT, code="BK-2", centre=80_000_000)

    assert drill.run(manifest=manifest).ok


def test_a_schema_behind_the_manifest_fails_the_drill(populated):
    """Restoring last month's dump into this month's code is legitimate. Discovering that you
    did so by accident, after the drill said everything was fine, is not."""
    manifest = a_manifest(migration_head={**backup.migration_head(), "audit": "0099_from_a_future"})

    report = drill.run(manifest=manifest)

    assert not report.ok
    assert any(check.name == "schema" for check in report.failures)


def test_without_credentials_the_drill_says_it_did_not_sign_in(populated):
    """A drill that quietly did less is worse than one that did less loudly."""
    report = drill.run()
    sign_in = next(check for check in report.checks if check.name == "sign-in")

    assert sign_in.ok
    assert "no sign-in was attempted" in sign_in.detail


def test_with_credentials_the_drill_actually_signs_in(populated):
    report = drill.run(username=populated["admin"].username, password=TEST_PASSWORD)
    sign_in = next(check for check in report.checks if check.name == "sign-in")

    assert sign_in.ok
    assert "signed in" in sign_in.detail


def test_a_wrong_password_fails_the_sign_in_check(populated):
    report = drill.run(username=populated["admin"].username, password="not the password")

    assert not report.ok
    assert any(check.name == "sign-in" for check in report.failures)


def test_a_database_with_no_administrator_fails_the_sign_in_check(populated):
    """A dump missing the role groups restores cleanly and lets nobody in."""
    from accounts.models import User

    User.objects.update(is_active=False)

    report = drill.run()

    assert not report.ok
    assert "no active administrator" in str(report.failures[0])


def test_an_empty_audit_trail_fails_the_drill(populated):
    """Every write in this product records an event, so an empty trail means a dropped table.

    The delete goes around the append-only trigger with `session_replication_role`, which is
    the only way to produce this state at all — and the fact that it takes a superuser and a
    replication setting is itself the point.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
        cursor.execute("DELETE FROM audit_event")
        cursor.execute("SET session_replication_role = DEFAULT")

    report = drill.run()

    assert not report.ok
    assert any(check.name == "audit trail" for check in report.failures)


def test_the_export_check_matches_what_the_table_would_show(populated):
    report = drill.run()
    export = next(check for check in report.checks if check.name == "export")

    assert export.ok
    assert "1 row(s)" in export.detail


def test_the_report_is_json_for_a_monitor_to_read(populated):
    payload = drill.run().as_dict()

    assert payload["ok"] is True
    assert {check["name"] for check in payload["checks"]}


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------
def test_backup_database_writes_a_dump_a_manifest_and_an_audit_event(tmp_path: Path, populated):
    """End to end against a real PostgreSQL, because a backup path mocked into passing is
    exactly the kind of thing that has never actually run."""
    call_command("backup_database", "--to", str(tmp_path), "--label", "drill")

    dumps = list(tmp_path.glob("*.dump"))
    assert len(dumps) == 1
    assert "drill" in dumps[0].name

    manifest = backup.read_manifest(dumps[0])
    assert manifest.dump_sha256 == backup.digest(dumps[0])
    assert manifest.row_counts["satnet_path"] >= 1

    event = AuditEvent.objects.get(action=BACKUP_TAKEN)
    assert event.after["sha256"] == manifest.dump_sha256


def test_a_written_manifest_is_readable_json(tmp_path: Path, populated):
    call_command("backup_database", "--to", str(tmp_path))
    manifest_file = next(tmp_path.glob("*.manifest.json"))

    payload = json.loads(manifest_file.read_text())

    assert payload["version"] == 1
    assert set(payload) == {field.name for field in dataclasses.fields(backup.Manifest)}


def test_restore_drill_records_its_own_outcome(populated):
    call_command("restore_drill")

    assert AuditEvent.objects.filter(action=RESTORE_DRILL_PASSED).exists()


def test_restore_drill_fails_the_command_when_a_check_fails(tmp_path: Path, populated):
    """A drill that reported a failure and exited zero would be worse than no drill."""
    manifest = a_manifest(row_counts={"satnet_path": 9_999})
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest.as_dict()))

    with pytest.raises(CommandError):
        call_command("restore_drill", "--manifest", str(path))


def test_verify_restore_refuses_a_dump_that_is_not_there(tmp_path: Path, populated):
    with pytest.raises(CommandError):
        call_command("verify_restore", "--dump", str(tmp_path / "absent.dump"), "--into", "scratch")
