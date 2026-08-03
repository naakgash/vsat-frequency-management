"""The authorization choke point.

Specification section 12: *"All permissions must be enforced in the backend, not only by
hiding buttons."* Section 18 additionally requires permission denials to be audited.

Both requirements are met by routing every authorization decision through :func:`require`.
Auditing denials is only tractable if denial happens in one place; a check scattered
across views would leave most denials unrecorded.

Two functions, with different jobs:

* :func:`allows` — a question. Returns a boolean, records nothing. Use it to decide
  whether to render a button.
* :func:`require` — an assertion. Raises :class:`~django.core.exceptions.PermissionDenied`
  and records an audit event. Use it at the top of every service function.

A view that only calls :func:`allows` is not protected. Templates ask; services require.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import connection

from accounts import scope
from audit import constants as audit_constants
from audit import services as audit_services

logger = logging.getLogger("django.security")


def allows(user: Any, capability: str, obj: Any = None) -> bool:
    """Does ``user`` hold ``capability``, and is ``obj`` within their scope?

    Silent: no audit event, no log entry. Asking whether to draw a button is not a
    security event.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if not user.has_perm(capability):
        return False
    return scope.is_in_scope(user, obj)


def require(user: Any, capability: str, obj: Any = None, *, reason: str = "") -> None:
    """Assert that ``user`` may exercise ``capability``, optionally against ``obj``.

    Raises :class:`PermissionDenied` after recording the denial. The exception message is
    intentionally generic — the audit record carries the detail, and telling a caller
    precisely which of capability or scope failed leaks the existence of objects they
    cannot see.
    """
    if not getattr(user, "is_authenticated", False):
        _record_denial(user, capability, obj, detail="not authenticated", reason=reason)
        raise PermissionDenied("Authentication is required for this action.")

    if not user.has_perm(capability):
        _record_denial(user, capability, obj, detail="capability not held", reason=reason)
        raise PermissionDenied("You do not have permission to perform this action.")

    if not scope.is_in_scope(user, obj):
        _record_denial(user, capability, obj, detail="object outside scope", reason=reason)
        raise PermissionDenied("You do not have permission to perform this action.")


def require_any(user: Any, capabilities: list[str], obj: Any = None) -> None:
    """Assert that ``user`` holds at least one of ``capabilities``.

    For actions reachable by more than one role through different capabilities — for
    example cancelling a draft, which both an Operator and an Approver may do.
    """
    if any(allows(user, capability, obj) for capability in capabilities):
        return
    _record_denial(user, " | ".join(capabilities), obj, detail="no matching capability")
    raise PermissionDenied("You do not have permission to perform this action.")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _record_denial(user: Any, capability: str, obj: Any, *, detail: str, reason: str = "") -> None:
    """Record a denial to the audit trail and the security log.

    Failures here must never mask the denial itself. If the audit write fails the user is
    still denied — the exception is logged and swallowed, because turning an audit
    outage into an authorization bypass would be strictly worse than an incomplete trail.
    """
    username = str(getattr(user, "get_username", lambda: "")() or "anonymous")
    message = f"Denied '{capability}': {detail}"

    if connection.in_atomic_block:
        # The caller authorised inside its own transaction, so the audit record written
        # below will be rolled back along with the denial. Services must authorise
        # before opening a transaction; this warning makes the lost record visible
        # rather than silent. tests/permissions/test_policy.py pins the expected shape.
        logger.error(
            "policy.require called inside a transaction for '%s'; the denial audit "
            "record will be rolled back. Authorise before opening the transaction.",
            capability,
        )

    try:
        audit_services.record(
            action=audit_constants.PERMISSION_DENIED,
            actor=user if getattr(user, "is_authenticated", False) else None,
            outcome=audit_constants.AuditOutcome.FAILURE,
            obj=obj if hasattr(obj, "_meta") else None,
            message=message,
            change_reason=reason,
        )
    except Exception:
        logger.exception("Failed to record permission denial for %s: %s", username, message)

    logger.warning("Permission denied for %s: %s", username, message)
