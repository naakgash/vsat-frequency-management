"""PostgreSQL capability tests.

These assert that the database can actually do what the design of the spectrum
reservation table assumes. They exist so a mis-provisioned or downgraded cluster fails
here, loudly, rather than at the point where an overlap silently goes unenforced.
"""

from __future__ import annotations

import pytest
from django.db import connection

from operations.health import REQUIRED_EXTENSIONS


@pytest.mark.django_db
def test_required_extensions_are_installed():
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}

    assert set(REQUIRED_EXTENSIONS) <= installed


@pytest.mark.django_db
def test_postgresql_is_version_16_or_newer():
    """Specification section 19.2 requires PostgreSQL 16 or a newer supported release."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        version_num = int(cursor.fetchone()[0])

    assert version_num >= 160000, f"PostgreSQL 16+ required, found {version_num}"


@pytest.mark.django_db
def test_range_constructors_are_immutable_enough_for_generated_columns():
    """docs/design/04 section 2 stores RF and time ranges as generated columns.

    A generated column requires an IMMUTABLE expression. Rather than introspecting
    ``pg_proc.provolatile``, this builds the exact construct the schema depends on: if
    PostgreSQL ever downgraded these constructors to STABLE, the CREATE TABLE fails and
    this test tells us before a migration does.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMPORARY TABLE generated_range_probe (
                start_hz    bigint      NOT NULL,
                end_hz      bigint      NOT NULL,
                valid_from  timestamptz NOT NULL,
                valid_until timestamptz NULL,
                rf_range    int8range   NOT NULL
                    GENERATED ALWAYS AS (int8range(start_hz, end_hz, '[)')) STORED,
                period      tstzrange   NOT NULL
                    GENERATED ALWAYS AS (tstzrange(valid_from, valid_until, '[)')) STORED
            ) ON COMMIT DROP
            """
        )
        cursor.execute(
            """
            INSERT INTO generated_range_probe (start_hz, end_hz, valid_from, valid_until)
            VALUES (29145000000, 29155500000, '2026-01-01T00:00:00Z', NULL)
            RETURNING rf_range, upper_inf(period)
            """
        )
        rf_range, period_unbounded = cursor.fetchone()

    # Half-open semantics, specification section 8.4: the upper bound is exclusive.
    assert rf_range.lower == 29145000000
    assert rf_range.upper == 29155500000
    assert rf_range.lower_inc is True
    assert rf_range.upper_inc is False
    # A NULL valid_until is an open-ended reservation, not a zero-length one.
    assert period_unbounded is True


@pytest.mark.django_db
def test_btree_gist_enables_a_mixed_equality_and_overlap_exclusion_constraint():
    """The overlap constraint mixes uuid/text equality with range overlap in one index.

    Stock GiST opclasses cannot do that; btree_gist is what makes it possible. This
    builds a miniature of the real constraint from docs/design/04 section 3 and proves
    both that it can be created and that it actually blocks an overlapping row.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TEMPORARY TABLE exclusion_probe (
                id                serial PRIMARY KEY,
                beam_id           uuid      NOT NULL,
                leg               text      NOT NULL,
                allocated_rf      int8range NOT NULL,
                active_period     tstzrange NOT NULL,
                reserves_spectrum boolean   NOT NULL,
                EXCLUDE USING gist (
                    beam_id       WITH =,
                    leg           WITH =,
                    allocated_rf  WITH &&,
                    active_period WITH &&
                ) WHERE (reserves_spectrum)
            ) ON COMMIT DROP
            """
        )

        insert = """
            INSERT INTO exclusion_probe
                (beam_id, leg, allocated_rf, active_period, reserves_spectrum)
            VALUES (%s, %s, int8range(%s, %s, '[)'),
                    tstzrange('2026-01-01T00:00:00Z', NULL, '[)'), %s)
        """
        beam = "11111111-1111-1111-1111-111111111111"

        cursor.execute(insert, [beam, "HUB_UPLINK", 29_145_000_000, 29_155_500_000, True])

        # Adjacent, not overlapping: half-open ranges make this legal (A-11).
        cursor.execute(insert, [beam, "HUB_UPLINK", 29_155_500_000, 29_160_000_000, True])

        # Non-reserving rows are outside the partial index entirely (A-12).
        cursor.execute(insert, [beam, "HUB_UPLINK", 29_145_000_000, 29_155_500_000, False])

        # A different leg is a different scope.
        cursor.execute(insert, [beam, "REMOTE_DOWNLINK", 29_145_000_000, 29_155_500_000, True])

        # Overlapping in the same scope must be refused by the database.
        with pytest.raises(Exception) as excinfo:
            cursor.execute(insert, [beam, "HUB_UPLINK", 29_150_000_000, 29_152_000_000, True])

    assert "exclusion" in str(excinfo.value).lower()
