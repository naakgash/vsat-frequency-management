"""Every URL must declare its authorization.

Specification section 12. The failure mode this guards against is mundane and common: a
new list view, or an HTMX fragment endpoint added later in a hurry, that nobody
remembers to protect. Reviewers miss it; a sweep of the URL configuration does not.

A view passes if it either enforces authorization itself, or is explicitly listed below
with a reason. Adding an unprotected view therefore requires a deliberate edit to this
file, which is the friction we want.
"""

from __future__ import annotations

from django.contrib.auth.mixins import (
    AccessMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.urls import URLPattern, URLResolver, get_resolver

# Endpoints reachable without authentication, and why.
PUBLIC_ENDPOINTS: dict[str, str] = {
    "home": "Landing page. Shows no scoped data; becomes the dashboard in S13.",
    "health-live": "Liveness probe. Polled by the orchestrator before any session exists.",
    "health-ready": "Readiness probe. Same reason; discloses only check names.",
    "accounts:login": "The sign-in form itself.",
    "accounts:logout": "Sign-out is safe for an anonymous caller; it is a no-op.",
}

# Views that delegate authorization to the service they call, and why that is correct.
SERVICE_AUTHORIZED: dict[str, str] = {
    "administration:user-assign-roles": (
        "Calls accounts.services.set_user_roles, which performs policy.require. "
        "Duplicating the check in the view invites the two copies to diverge."
    ),
}


def _iter_patterns(resolver, prefix: str = ""):
    """Yield (fully qualified url name, callback) for every routable pattern."""
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            namespace = f"{prefix}{entry.namespace}:" if entry.namespace else prefix
            yield from _iter_patterns(entry, namespace)
        elif isinstance(entry, URLPattern):
            if entry.name is None:
                continue
            yield f"{prefix}{entry.name}", entry.callback


def _enforces_authorization(callback) -> bool:
    """Does this view enforce authorization by itself?"""
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        if issubclass(view_class, PermissionRequiredMixin | LoginRequiredMixin | AccessMixin):
            return True
        if getattr(view_class, "permission_required", None):
            return True
    # login_required and permission_required decorators leave this marker behind.
    return bool(getattr(callback, "login_url", None))


def test_every_url_declares_its_authorization():
    undeclared = []

    for name, callback in _iter_patterns(get_resolver()):
        if name in PUBLIC_ENDPOINTS or name in SERVICE_AUTHORIZED:
            continue
        if _enforces_authorization(callback):
            continue
        undeclared.append(name)

    assert not undeclared, (
        "These URLs enforce no authorization and are not listed as public:\n  "
        + "\n  ".join(sorted(undeclared))
        + "\n\nAdd a PermissionRequiredMixin to the view, or add the URL to "
        "PUBLIC_ENDPOINTS / SERVICE_AUTHORIZED in this file with a reason."
    )


def test_the_sweep_actually_finds_urls():
    """A sweep that enumerates nothing would pass forever."""
    names = [name for name, _ in _iter_patterns(get_resolver())]

    assert len(names) >= 7, f"URL sweep found only {names}"
    assert "administration:user-list" in names


def test_allowlist_entries_still_exist():
    """Remove a URL and its allowlist entry must go too, or the next view to reuse the
    name silently inherits an exemption."""
    names = {name for name, _ in _iter_patterns(get_resolver())}
    stale = sorted((set(PUBLIC_ENDPOINTS) | set(SERVICE_AUTHORIZED)) - names)

    assert not stale, f"allowlist entries reference URLs that no longer exist: {stale}"


def test_every_allowlist_entry_has_a_reason():
    for name, reason in {**PUBLIC_ENDPOINTS, **SERVICE_AUTHORIZED}.items():
        assert reason.strip(), f"{name} is allow-listed without a reason"
