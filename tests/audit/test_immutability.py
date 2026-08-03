"""Audit is append-only, enforced by the database.

Specification section 18 and design assumption A-15. Every test here goes around the ORM
where it can, because the guarantee is worthless if it only holds for well-behaved
Python.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction

from audit import constants
from audit.models import AuditEvent
from audit.services import record

# The trigger raises with SQLSTATE 23001 (restrict_violation), which is in the 23xxx
# integrity-constraint class, so Django surfaces it as IntegrityError.
TRIGGER_ERRORS = IntegrityError


@pytest.fixture
def event(db) -> AuditEvent:
    return record(action=constants.USER_LOGGED_IN, message="original message")


@pytest.mark.django_db
def test_update_through_the_orm_is_rejected(event):
    event.message = "tampered"

    with pytest.raises(TRIGGER_ERRORS) as excinfo, transaction.atomic():
        event.save()

    assert "append-only" in str(excinfo.value)


@pytest.mark.django_db
def test_queryset_update_is_rejected(event):
    """The path a Python-level save() guard would miss entirely."""
    with pytest.raises(TRIGGER_ERRORS), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).update(message="tampered")


@pytest.mark.django_db
def test_delete_through_the_orm_is_rejected(event):
    with pytest.raises(TRIGGER_ERRORS), transaction.atomic():
        event.delete()


@pytest.mark.django_db
def test_queryset_delete_is_rejected(event):
    with pytest.raises(TRIGGER_ERRORS), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).delete()


@pytest.mark.django_db
def test_raw_sql_update_is_rejected(event):
    """The case that matters most: a maintenance script or a psql session."""
    with pytest.raises(TRIGGER_ERRORS), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE audit_event SET message = 'tampered' WHERE id = %s", [event.pk])


@pytest.mark.django_db
def test_raw_sql_delete_is_rejected(event):
    with pytest.raises(TRIGGER_ERRORS), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM audit_event WHERE id = %s", [event.pk])


@pytest.mark.django_db
def test_the_record_survives_every_attempt(event):
    """After all of the above, the original row must be intact and unchanged."""
    for attempt in (
        lambda: AuditEvent.objects.filter(pk=event.pk).update(message="tampered"),
        lambda: AuditEvent.objects.filter(pk=event.pk).delete(),
    ):
        with pytest.raises(TRIGGER_ERRORS), transaction.atomic():
            attempt()

    survivor = AuditEvent.objects.get(pk=event.pk)
    assert survivor.message == "original message"


@pytest.mark.django_db
def test_inserting_is_still_permitted():
    """Append-only means append is allowed. A trigger that blocked INSERT too would
    silently disable the entire audit trail."""
    before = AuditEvent.objects.count()

    record(action=constants.USER_LOGGED_OUT)

    assert AuditEvent.objects.count() == before + 1


@pytest.mark.django_db
def test_no_write_permissions_exist_for_any_role():
    """docs/design/03 section 2.1: no role, including admin, may edit audit."""
    from django.contrib.auth.models import Permission

    codenames = set(
        Permission.objects.filter(content_type__app_label="audit").values_list(
            "codename", flat=True
        )
    )

    assert codenames == {"view_auditevent", "view_all_auditevent"}
    assert not any(c.startswith(("add_", "change_", "delete_")) for c in codenames)
