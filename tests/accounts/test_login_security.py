"""Login throttling, temporary lockout and authentication auditing.

Specification sections 21.4, 21.5 and 18.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import services
from accounts.models import LoginAttempt
from audit import constants as audit_constants
from audit.models import AuditEvent
from tests.factories import TEST_PASSWORD, make_operator

LOGIN_URL = "/accounts/login/"


def _fail_login(client, username: str, times: int = 1) -> None:
    for _ in range(times):
        client.post(LOGIN_URL, {"username": username, "password": "definitely-wrong"})


@pytest.mark.django_db
def test_successful_login_is_audited(client):
    user = make_operator("planner")

    response = client.post(
        LOGIN_URL, {"username": "planner", "password": TEST_PASSWORD}, follow=False
    )

    assert response.status_code == 302
    event = AuditEvent.objects.get(action=audit_constants.USER_LOGGED_IN)
    assert event.actor_id == user.pk
    assert event.outcome == audit_constants.AuditOutcome.SUCCESS


@pytest.mark.django_db
def test_failed_login_is_audited(client):
    make_operator("planner")

    _fail_login(client, "planner")

    event = AuditEvent.objects.get(action=audit_constants.USER_LOGIN_FAILED)
    assert event.outcome == audit_constants.AuditOutcome.FAILURE
    assert event.object_repr == "planner"


@pytest.mark.django_db
def test_failed_login_for_an_unknown_username_is_still_recorded(client):
    """Attempts against a non-existent account are exactly the ones worth counting."""
    _fail_login(client, "no-such-person")

    assert LoginAttempt.objects.filter(username="no-such-person", successful=False).count() == 1


@pytest.mark.django_db
def test_logout_is_audited(client):
    make_operator("planner")
    client.login(username="planner", password=TEST_PASSWORD)

    client.post("/accounts/logout/")

    assert AuditEvent.objects.filter(action=audit_constants.USER_LOGGED_OUT).exists()


@pytest.mark.django_db
def test_account_locks_after_the_configured_number_of_failures(client, settings):
    settings.LOGIN_FAILURE_LIMIT = 3
    make_operator("planner")

    _fail_login(client, "planner", times=3)

    # Correct credentials are now refused: the lockout is checked before the password.
    response = client.post(LOGIN_URL, {"username": "planner", "password": TEST_PASSWORD})

    assert response.status_code == 200  # form redisplayed, not signed in
    assert "temporarily locked" in response.content.decode().lower()
    assert AuditEvent.objects.filter(action=audit_constants.USER_LOCKED_OUT).exists()


@pytest.mark.django_db
def test_lockout_is_checked_before_credentials_are_verified(client, settings):
    """Validating the password first would let an attacker confirm a correct password
    even while locked out, which is most of the value of having the password."""
    settings.LOGIN_FAILURE_LIMIT = 2
    make_operator("planner")
    _fail_login(client, "planner", times=2)

    response = client.post(LOGIN_URL, {"username": "planner", "password": TEST_PASSWORD})

    assert "temporarily locked" in response.content.decode().lower()
    assert response.wsgi_request.user.is_anonymous


@pytest.mark.django_db
def test_locking_one_account_does_not_lock_another_from_the_same_address(client, settings):
    """The per-address limit must stay far above the per-username limit.

    Operators routinely share a source address behind NAT or a VPN concentrator. A
    combined counter, or an address limit near the username limit, would let one
    person's mistyped password lock out an entire site.
    """
    settings.LOGIN_FAILURE_LIMIT = 3
    settings.LOGIN_IP_FAILURE_LIMIT = 50
    make_operator("planner")
    make_operator("reviewer")

    _fail_login(client, "planner", times=5)

    response = client.post(LOGIN_URL, {"username": "reviewer", "password": TEST_PASSWORD})

    assert response.status_code == 302, "a second account was locked out by the first"


@pytest.mark.django_db
def test_password_spraying_across_accounts_trips_the_address_limit(client, settings):
    """Each account stays below its own threshold, but the address total does not.

    Driven through the test client rather than by calling the service directly: the
    source address comes from the request context, so a direct call has no address at
    all and would silently exercise only the per-username path.
    """
    settings.LOGIN_FAILURE_LIMIT = 5
    settings.LOGIN_IP_FAILURE_LIMIT = 6
    for index in range(4):
        make_operator(f"user{index}")

    for index in range(4):
        _fail_login(client, f"user{index}", times=2)  # 2 each: under 5, but 8 in total

    assert LoginAttempt.objects.filter(successful=False, source_ip="127.0.0.1").count() == 8

    # A fresh account, well under its own limit, is now refused from this address.
    make_operator("bystander")
    response = client.post(LOGIN_URL, {"username": "bystander", "password": TEST_PASSWORD})

    assert response.status_code == 200
    assert "temporarily locked" in response.content.decode().lower()


@pytest.mark.django_db
def test_there_is_no_address_lockout_outside_a_request():
    """A management command has no source address, and must not be locked out by one."""
    state = services.lockout_state(username="planner")

    assert state.ip_failures == 0
    assert state.locked is False


@pytest.mark.django_db
def test_lockout_expires_as_failures_age_out(client, settings):
    """A rolling window, not a sticky flag: no administrator intervention required."""
    settings.LOGIN_FAILURE_LIMIT = 3
    settings.LOGIN_LOCKOUT_SECONDS = 900
    make_operator("planner")
    _fail_login(client, "planner", times=3)
    assert services.lockout_state(username="planner").locked

    # Age the failures past the window.
    LoginAttempt.objects.update(occurred_at=timezone.now() - timedelta(seconds=1000))

    assert services.lockout_state(username="planner").locked is False
    response = client.post(LOGIN_URL, {"username": "planner", "password": TEST_PASSWORD})
    assert response.status_code == 302


@pytest.mark.django_db
def test_login_error_does_not_reveal_whether_the_username_exists(client):
    """A distinguishable message turns the login form into a username oracle."""
    make_operator("planner")

    known = client.post(LOGIN_URL, {"username": "planner", "password": "wrong"})
    unknown = client.post(LOGIN_URL, {"username": "ghost", "password": "wrong"})

    assert "Enter a correct username and password" in known.content.decode()
    assert "Enter a correct username and password" in unknown.content.decode()


@pytest.mark.django_db
def test_successful_login_records_the_attempt(client):
    make_operator("planner")

    client.post(LOGIN_URL, {"username": "planner", "password": TEST_PASSWORD})

    assert LoginAttempt.objects.filter(username="planner", successful=True).exists()
