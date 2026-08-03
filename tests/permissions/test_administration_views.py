"""Administration views enforce authorization in the backend.

Specification section 12: *"All permissions must be enforced in the backend, not only by
hiding buttons."* Every write here is a direct HTTP POST rather than a rendered form
submission, so a hidden button can never be mistaken for an enforced rule.
"""

from __future__ import annotations

import pytest

from accounts.constants import Role
from audit import constants as audit_constants
from audit.models import AuditEvent
from tests.factories import (
    TEST_PASSWORD,
    make_admin,
    make_approver,
    make_observer,
    make_operator,
    make_user,
)

USER_LIST = "/administration/users/"


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


@pytest.mark.django_db
def test_anonymous_is_redirected_to_sign_in(client):
    response = client.get(USER_LIST)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_approver, make_observer])
def test_non_admin_roles_are_refused(client, factory):
    _sign_in(client, factory())

    assert client.get(USER_LIST).status_code == 403


@pytest.mark.django_db
def test_admin_may_list_users(client):
    admin = make_admin()
    make_operator("planner")
    _sign_in(client, admin)

    response = client.get(USER_LIST)

    assert response.status_code == 200
    assert "planner" in response.content.decode()


@pytest.mark.django_db
def test_admin_may_view_a_user(client):
    admin = make_admin()
    subject = make_operator("planner")
    _sign_in(client, admin)

    response = client.get(subject.get_absolute_url())

    assert response.status_code == 200
    assert "planner" in response.content.decode()


@pytest.mark.django_db
def test_operator_cannot_assign_roles_by_direct_post(client):
    """The button is hidden from an Operator. That is not the control being tested."""
    operator = make_operator()
    subject = make_user("target")
    _sign_in(client, operator)

    response = client.post(f"/administration/users/{subject.pk}/roles/", {"roles": [Role.ADMIN]})

    assert response.status_code == 403
    subject.refresh_from_db()
    assert subject.role_names == frozenset()


@pytest.mark.django_db
def test_observer_cannot_escalate_their_own_role(client):
    """The specific attack the capability check exists to stop."""
    observer = make_observer("watcher")
    _sign_in(client, observer)

    response = client.post(f"/administration/users/{observer.pk}/roles/", {"roles": [Role.ADMIN]})

    assert response.status_code == 403
    observer.refresh_from_db()
    assert observer.is_admin is False


@pytest.mark.django_db
def test_admin_may_assign_roles(client):
    admin = make_admin()
    subject = make_user("target")
    _sign_in(client, admin)

    response = client.post(
        f"/administration/users/{subject.pk}/roles/",
        {"roles": [Role.OPERATOR, Role.APPROVER], "reason": "Joined the planning team"},
    )

    assert response.status_code == 302
    subject.refresh_from_db()
    assert subject.role_names == frozenset({Role.OPERATOR, Role.APPROVER})


@pytest.mark.django_db
def test_role_assignment_is_audited_with_a_before_and_after(client):
    """Specification section 18 requires role changes to be audited with field-level
    before and after values."""
    admin = make_admin()
    subject = make_operator("planner")
    _sign_in(client, admin)

    client.post(
        f"/administration/users/{subject.pk}/roles/",
        {"roles": [Role.APPROVER], "reason": "Moved to approvals"},
    )

    event = AuditEvent.objects.get(action=audit_constants.USER_ROLES_CHANGED)
    assert event.actor_id == admin.pk
    assert event.object_id == subject.pk
    assert event.before == {"roles": [Role.OPERATOR]}
    assert event.after == {"roles": [Role.APPROVER]}
    assert event.change_reason == "Moved to approvals"


@pytest.mark.django_db
def test_denied_role_assignment_is_audited(client):
    operator = make_operator()
    subject = make_user("target")
    _sign_in(client, operator)

    client.post(f"/administration/users/{subject.pk}/roles/", {"roles": [Role.ADMIN]})

    assert AuditEvent.objects.filter(
        action=audit_constants.PERMISSION_DENIED,
        actor=operator,
        outcome=audit_constants.AuditOutcome.FAILURE,
    ).exists()


@pytest.mark.django_db
def test_unknown_roles_are_rejected(client):
    admin = make_admin()
    subject = make_user("target")
    _sign_in(client, admin)

    response = client.post(f"/administration/users/{subject.pk}/roles/", {"roles": ["superadmin"]})

    assert response.status_code == 400
    subject.refresh_from_db()
    assert subject.role_names == frozenset()


@pytest.mark.django_db
def test_clearing_all_roles_is_permitted_and_removes_access(client):
    admin = make_admin()
    subject = make_operator("planner")
    _sign_in(client, admin)

    client.post(f"/administration/users/{subject.pk}/roles/", {"roles": []})

    subject.refresh_from_db()
    assert subject.role_names == frozenset()


@pytest.mark.django_db
def test_the_administration_link_is_hidden_from_non_admins(client):
    """Cosmetic, but still worth asserting: a visible link that 403s is a poor
    experience, and the hiding must track the same capability the view enforces."""
    _sign_in(client, make_operator())
    operator_nav = client.get("/").content.decode()

    client.logout()
    _sign_in(client, make_admin())
    admin_nav = client.get("/").content.decode()

    assert 'href="/administration/users/"' not in operator_nav
    assert 'href="/administration/users/"' in admin_nav
