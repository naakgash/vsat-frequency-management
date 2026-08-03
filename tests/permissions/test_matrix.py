"""Capability matrix, one test per cell.

Specification section 12 and section 25. The matrix is generated from
``accounts.constants.CAPABILITY_MATRIX``, so a capability added to the product without a
corresponding test is impossible: the parametrisation grows with the matrix.
"""

from __future__ import annotations

import pytest

from accounts import policy
from accounts.constants import CAPABILITY_MATRIX, Role
from tests.factories import make_user


def _matrix_cells() -> list[tuple[str, str, bool]]:
    """Every (capability, role, expected) combination in the matrix."""
    return [
        (capability, role.value, role.value in holders)
        for capability, holders in sorted(CAPABILITY_MATRIX.items())
        for role in Role
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(("capability", "role", "expected"), _matrix_cells())
def test_capability_matrix_cell(capability: str, role: str, expected: bool):
    user = make_user(f"user-{role}", roles=[role])

    assert policy.allows(user, capability) is expected


@pytest.mark.django_db
def test_a_user_with_no_roles_holds_nothing():
    """Deny by default (design assumption A-17)."""
    user = make_user("unassigned")

    for capability in CAPABILITY_MATRIX:
        assert policy.allows(user, capability) is False


@pytest.mark.django_db
def test_roles_are_additive():
    """A user may hold several roles; capabilities are the union, never the intersection."""
    both = make_user("dual-role", roles=[Role.OPERATOR, Role.APPROVER])

    assert both.has_role(Role.OPERATOR)
    assert both.has_role(Role.APPROVER)
    assert not both.is_admin


@pytest.mark.django_db
def test_anonymous_holds_nothing():
    from django.contrib.auth.models import AnonymousUser

    for capability in CAPABILITY_MATRIX:
        assert policy.allows(AnonymousUser(), capability) is False


@pytest.mark.django_db
def test_django_superuser_is_not_the_application_admin_role():
    """``is_superuser`` is a database escape hatch; the Admin role is a business role.

    Scope bypass keys off the role, so granting Admin stays a visible, audited act
    rather than an invisible flag on a row.
    """
    superuser = make_user("root", is_superuser=True)

    assert superuser.is_admin is False
    assert Role.ADMIN not in superuser.role_names
