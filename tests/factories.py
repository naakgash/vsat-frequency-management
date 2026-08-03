"""Test object builders.

Plain functions rather than a factory library: at this stage the objects are simple, and
a function whose signature is the model's own fields is easier to read in a failing test
than a factory class.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Group

from accounts.constants import Role
from accounts.models import User

TEST_PASSWORD = "correct-horse-battery-staple"


def make_user(
    username: str = "someone",
    *,
    roles: list[str] | None = None,
    password: str = TEST_PASSWORD,
    **extra: Any,
) -> User:
    """Create a user with the given roles."""
    user = User.objects.create_user(
        username=username,
        email=extra.pop("email", f"{username}@example.invalid"),
        password=password,
        **extra,
    )
    if roles:
        user.groups.set(Group.objects.filter(name__in=roles))
    return user


def make_admin(username: str = "an-admin", **extra: Any) -> User:
    return make_user(username, roles=[Role.ADMIN], **extra)


def make_operator(username: str = "an-operator", **extra: Any) -> User:
    return make_user(username, roles=[Role.OPERATOR], **extra)


def make_approver(username: str = "an-approver", **extra: Any) -> User:
    return make_user(username, roles=[Role.APPROVER], **extra)


def make_observer(username: str = "an-observer", **extra: Any) -> User:
    return make_user(username, roles=[Role.OBSERVER], **extra)
