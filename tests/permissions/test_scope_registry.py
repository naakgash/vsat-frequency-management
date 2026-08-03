"""Scope resolver registry.

docs/design/03 section 3 and design assumption A-17. The scope *tables* land with their
target models in slices S4 and S8; the machinery lands here, and these tests pin its
semantics so those slices only have to supply a resolver.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts import scope
from accounts.models import LoginAttempt
from tests.factories import make_admin, make_observer, make_operator


@pytest.fixture
def scoped_model():
    """Register a scope rule against a real model, then remove it.

    ``LoginAttempt`` stands in for a domain model: it is real, it is not otherwise
    scope-controlled, and using it avoids inventing a test-only model that would need a
    migration.
    """
    # In scope only when the attempt is for the user's own username.
    scope.register(LoginAttempt, lambda user, obj: obj.username == user.get_username())
    yield LoginAttempt
    scope.unregister(LoginAttempt)


@pytest.mark.django_db
def test_unregistered_models_are_not_scoped():
    """A model with no resolver is global master data, not a security hole.

    Guarded by test_every_designed_scoped_model_has_a_resolver below.
    """
    operator = make_operator()
    attempt = LoginAttempt(username="someone-else", successful=False)

    assert scope.is_in_scope(operator, attempt) is True


@pytest.mark.django_db
def test_resolver_is_consulted_for_a_registered_model(scoped_model):
    operator = make_operator("planner")

    own = LoginAttempt(username="planner", successful=False)
    other = LoginAttempt(username="someone-else", successful=False)

    assert scope.is_in_scope(operator, own) is True
    assert scope.is_in_scope(operator, other) is False


@pytest.mark.django_db
def test_admin_bypasses_scope(scoped_model):
    """docs/design/03 section 3.2. Checked centrally so no resolver can forget it."""
    admin = make_admin()
    other = LoginAttempt(username="someone-else", successful=False)

    assert scope.is_in_scope(admin, other) is True


@pytest.mark.django_db
def test_anonymous_is_denied_a_scoped_object(scoped_model):
    attempt = LoginAttempt(username="anything", successful=False)

    assert scope.is_in_scope(AnonymousUser(), attempt) is False


@pytest.mark.django_db
def test_non_admin_roles_do_not_bypass_scope(scoped_model):
    observer = make_observer("watcher")
    other = LoginAttempt(username="someone-else", successful=False)

    assert scope.is_in_scope(observer, other) is False


def test_none_is_always_in_scope():
    """A capability check with no object is a capability check, not a scope check."""
    assert scope.is_in_scope(AnonymousUser(), None) is True


def test_registration_is_idempotent(db):
    """Django may run AppConfig.ready() more than once under autoreload and in tests."""
    scope.register(LoginAttempt, lambda user, obj: True)
    scope.register(LoginAttempt, lambda user, obj: False)
    try:
        assert scope.is_scoped(LoginAttempt)
        assert len([m for m in scope.registered_models() if m is LoginAttempt]) == 1
    finally:
        scope.unregister(LoginAttempt)


def test_every_designed_scoped_model_has_a_resolver():
    """Models the design marks as scope-controlled must register a resolver.

    Because an unregistered model is treated as unscoped, forgetting to register one
    would silently expose it. This list grows as the slices that introduce these models
    land; until then it documents the intent and asserts nothing prematurely.
    """
    expected_scoped_by_slice = {
        # slice -> dotted model paths that must be registered by the time it lands
        "S4": ["inventory.Gateway", "inventory.Hub"],
        "S8": ["beams.Beam"],
        "S10": ["satnets.Satnet"],
        "S11": ["satnet_paths.SatnetPath"],
    }

    from django.apps import apps

    registered = {f"{m._meta.app_label}.{m._meta.object_name}" for m in scope.registered_models()}

    for _slice_name, model_paths in expected_scoped_by_slice.items():
        for path in model_paths:
            app_label, model_name = path.split(".")
            try:
                apps.get_model(app_label, model_name)
            except LookupError:
                continue  # the slice that introduces it has not landed yet
            assert path in registered, (
                f"{path} exists but has no scope resolver. Register it in the module's "
                f"AppConfig.ready(), or the model will be treated as unscoped."
            )
