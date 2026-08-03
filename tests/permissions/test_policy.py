"""The policy choke point: denial behaviour and denial auditing.

Specification sections 12 and 18.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from accounts import policy
from accounts.constants import MANAGE_USERS, VIEW_AUDIT, Role
from audit import constants as audit_constants
from audit.models import AuditEvent
from tests.factories import make_admin, make_observer, make_user


@pytest.mark.django_db
def test_require_passes_for_a_holder():
    admin = make_admin()

    policy.require(admin, MANAGE_USERS)  # must not raise


@pytest.mark.django_db
def test_require_raises_for_a_non_holder():
    observer = make_observer()

    with pytest.raises(PermissionDenied):
        policy.require(observer, MANAGE_USERS)


@pytest.mark.django_db
def test_require_raises_for_anonymous():
    with pytest.raises(PermissionDenied):
        policy.require(AnonymousUser(), VIEW_AUDIT)


@pytest.mark.django_db
def test_denial_is_audited():
    """Specification section 18 requires permission denials to be recorded.

    This is the reason authorization goes through one function: denials scattered across
    views would mostly go unrecorded.
    """
    observer = make_observer()

    with pytest.raises(PermissionDenied):
        policy.require(observer, MANAGE_USERS)

    event = AuditEvent.objects.filter(action=audit_constants.PERMISSION_DENIED).latest(
        "occurred_at"
    )
    assert event.outcome == audit_constants.AuditOutcome.FAILURE
    assert event.actor_id == observer.pk
    assert MANAGE_USERS in event.message


@pytest.mark.django_db
def test_anonymous_denial_is_audited_without_an_actor():
    with pytest.raises(PermissionDenied):
        policy.require(AnonymousUser(), MANAGE_USERS)

    event = AuditEvent.objects.filter(action=audit_constants.PERMISSION_DENIED).latest(
        "occurred_at"
    )
    assert event.actor_id is None
    assert "not authenticated" in event.message


@pytest.mark.django_db
def test_allows_records_nothing():
    """Asking whether to draw a button is not a security event."""
    observer = make_observer()
    before = AuditEvent.objects.count()

    assert policy.allows(observer, MANAGE_USERS) is False

    assert AuditEvent.objects.count() == before


@pytest.mark.django_db
def test_denial_message_does_not_reveal_which_check_failed():
    """A message distinguishing 'no capability' from 'out of scope' leaks the existence
    of objects the caller cannot see. The detail belongs in the audit record."""
    observer = make_observer()

    with pytest.raises(PermissionDenied) as excinfo:
        policy.require(observer, MANAGE_USERS)

    message = str(excinfo.value)
    assert "scope" not in message.lower()
    assert "capability" not in message.lower()


@pytest.mark.django_db
def test_require_any_accepts_a_single_matching_capability():
    operator = make_user("multi", roles=[Role.OPERATOR])

    policy.require_any(operator, [MANAGE_USERS, VIEW_AUDIT])  # holds the second


@pytest.mark.django_db
def test_require_any_raises_when_none_match():
    observer = make_observer()

    with pytest.raises(PermissionDenied):
        policy.require_any(observer, [MANAGE_USERS, VIEW_AUDIT])


@pytest.mark.django_db(transaction=True)
def test_denial_audit_survives_when_the_service_authorises_first(seeded_roles):
    """Services must authorise *before* opening their transaction.

    ``policy.require`` writes the denial then raises. If the caller had already opened a
    transaction, the rollback would discard exactly the record specification section 18
    most wants kept. This asserts the real service does it in the right order.
    """
    from accounts import services

    operator = make_user("planner", roles=[Role.OPERATOR])
    target = make_user("target")
    AuditEvent.objects.all().delete()

    with pytest.raises(PermissionDenied):
        services.set_user_roles(actor=operator, user=target, roles=[Role.ADMIN])

    assert AuditEvent.objects.filter(action=audit_constants.PERMISSION_DENIED).exists()


@pytest.mark.django_db
def test_denial_inside_a_transaction_is_logged_as_an_error(caplog):
    """Defence in depth for the rule above.

    If a future service authorises inside its transaction, the audit record is lost. That
    must be noisy rather than silent, so the mistake is discoverable in the logs.
    """
    import logging

    from django.db import transaction

    observer = make_observer()

    # django.security is configured with propagate=False so security records do not
    # duplicate into the root handler. caplog attaches to the root logger, so its handler
    # has to be attached to this logger directly or it would capture nothing — and the
    # test would pass or fail for reasons unrelated to the behaviour under test.
    security_logger = logging.getLogger("django.security")
    security_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.ERROR, logger="django.security"):
            with pytest.raises(PermissionDenied), transaction.atomic():
                policy.require(observer, MANAGE_USERS)
    finally:
        security_logger.removeHandler(caplog.handler)

    assert any("rolled back" in record.getMessage() for record in caplog.records)


@pytest.mark.django_db
def test_audit_failure_does_not_swallow_the_denial(monkeypatch):
    """If the audit write fails, the user must still be denied.

    Turning an audit outage into an authorization bypass would be strictly worse than an
    incomplete trail.
    """
    from audit import services as audit_services

    def explode(**kwargs):
        raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr(audit_services, "record", explode)
    observer = make_observer()

    with pytest.raises(PermissionDenied):
        policy.require(observer, MANAGE_USERS)
