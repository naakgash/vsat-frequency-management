"""Object-level authorization scope.

Specification section 6: an Operator may act "only within Beams, Hubs, and Gateways
included in that user's authorization scope". Design assumption A-17 makes that deny by
default and conjunctive, with Admin bypassing scope entirely.

**Why a registry.** The scope tables have foreign keys to Gateway, Hub and Beam, which
arrive in slices S4 and S8. If :mod:`accounts` imported those models it would invert the
module dependency direction of docs/design/01, where accounts is a cross-cutting module
that domain modules import and that imports none of them.

So domain modules register their own scope resolver as they land::

    # inventory/apps.py, in ready()
    from accounts import scope
    scope.register(Gateway, resolve_gateway_scope)

Nothing here imports a domain module, and adding a scoped model never requires editing
this file.

**Unregistered models are in scope.** A model with no resolver is not scoped — the
Specification Dictionary, for instance, is global master data. This is a deliberate
default, guarded by ``tests/permissions/test_scope_registry.py``, which asserts that
every model the design marks as scoped has a resolver registered by the time its slice
lands.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from django.db import models

Model = TypeVar("Model", bound=models.Model)

#: Resolver signature: given a user and an object, is the object within the user's scope?
ScopeResolver = Callable[[Any, Any], bool]

_RESOLVERS: dict[type[models.Model], ScopeResolver] = {}


def register(model: type[Model], resolver: Callable[[Any, Model], bool]) -> None:
    """Register the scope rule for a model.

    Re-registering the same model replaces the resolver. That is intentional: Django may
    import an ``AppConfig.ready()`` more than once in some test and autoreload
    configurations, and a duplicate-registration error there would be noise rather than
    a signal.
    """
    _RESOLVERS[model] = resolver


def unregister(model: type[models.Model]) -> None:
    """Remove a model's scope rule. Used by tests to restore a clean registry."""
    _RESOLVERS.pop(model, None)


def registered_models() -> frozenset[type[models.Model]]:
    """Models that currently have a scope rule."""
    return frozenset(_RESOLVERS)


def is_scoped(model: type[models.Model]) -> bool:
    return model in _RESOLVERS


def is_in_scope(user: Any, obj: Any) -> bool:
    """Is ``obj`` within ``user``'s authorization scope?

    Returns True for an unscoped model, and for any object when the user holds the Admin
    role. Returns False for an unauthenticated user against a scoped object.
    """
    if obj is None:
        return True

    resolver = _RESOLVERS.get(type(obj))
    if resolver is None:
        return True

    if not getattr(user, "is_authenticated", False):
        return False

    # Admin bypasses scope (docs/design/03 section 3.2). Checked here, once, rather than
    # in every resolver, so a resolver cannot forget it.
    if getattr(user, "is_admin", False):
        return True

    return resolver(user, obj)
