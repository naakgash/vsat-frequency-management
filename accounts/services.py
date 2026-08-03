"""Account write paths: authentication attempts, lockout, role assignment."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import transaction

from accounts import policy
from accounts.constants import MANAGE_USERS, Role
from accounts.models import LoginAttempt, User
from accounts.types import Actor
from audit import constants as audit_constants
from audit import context as audit_context
from audit import services as audit_services


def _failure_limit() -> int:
    return int(getattr(settings, "LOGIN_FAILURE_LIMIT", 5))


def _ip_failure_limit() -> int:
    return int(getattr(settings, "LOGIN_IP_FAILURE_LIMIT", 50))


def _lockout_window() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", 900)))


@dataclasses.dataclass(frozen=True)
class LockoutState:
    """Whether authentication is currently refused, and on which basis."""

    locked: bool
    username_failures: int
    username_limit: int
    ip_failures: int
    ip_limit: int
    window_seconds: int
    #: Which threshold triggered the lockout: "username", "ip", or "" when not locked.
    triggered_by: str = ""

    @property
    def remaining_attempts(self) -> int:
        return max(0, self.username_limit - self.username_failures)


def lockout_state(*, username: str) -> LockoutState:
    """Evaluate the temporary lockout for a username and the current source address.

    Specification sections 21.4 and 21.5. Two independent thresholds, because they defend
    against different attacks and a single combined counter defends against neither well:

    * **per username** (strict) — stops a targeted attack that rotates source addresses;
    * **per source address** (loose) — stops password spraying across many accounts,
      where no single account reaches its own limit.

    The address limit is deliberately far higher. Operators routinely share a source
    address behind NAT or a VPN concentrator, and an address limit near the username
    limit would let one person's mistyped password lock out an entire site.

    The lockout is temporary and rolling: it expires as failures age out of the window,
    with no administrator intervention. A permanent lockout would turn a trivial
    password-spray into a denial of service against real operators.
    """
    request = audit_context.current()
    window = _lockout_window()

    username_failures = LoginAttempt.recent_failures_for_username(username=username, window=window)
    ip_failures = LoginAttempt.recent_failures_for_ip(source_ip=request.source_ip, window=window)
    username_limit = _failure_limit()
    ip_limit = _ip_failure_limit()

    triggered_by = ""
    if username_failures >= username_limit:
        triggered_by = "username"
    elif ip_failures >= ip_limit:
        triggered_by = "ip"

    return LockoutState(
        locked=bool(triggered_by),
        username_failures=username_failures,
        username_limit=username_limit,
        ip_failures=ip_failures,
        ip_limit=ip_limit,
        window_seconds=int(window.total_seconds()),
        triggered_by=triggered_by,
    )


def record_login_attempt(*, username: str, successful: bool) -> LoginAttempt:
    """Persist an authentication attempt for rate limiting."""
    request = audit_context.current()
    return LoginAttempt.objects.create(
        username=username[:150],
        successful=successful,
        source_ip=request.source_ip,
        user_agent=request.user_agent[:512],
    )


def register_successful_login(*, user: User) -> None:
    """Record a successful sign-in (specification section 18)."""
    record_login_attempt(username=user.get_username(), successful=True)
    audit_services.record(
        action=audit_constants.USER_LOGGED_IN,
        actor=user,
        obj=user,
        message=f"Signed in with roles: {', '.join(sorted(user.role_names)) or 'none'}",
    )


def register_failed_login(*, username: str, locked_out: bool) -> None:
    """Record a failed sign-in, and the lockout it triggered if any."""
    record_login_attempt(username=username, successful=False)
    audit_services.record(
        action=audit_constants.USER_LOGIN_FAILED,
        outcome=audit_constants.AuditOutcome.FAILURE,
        object_type="accounts.User",
        object_repr=username[:255],
        message="Invalid credentials",
    )
    if locked_out:
        audit_services.record(
            action=audit_constants.USER_LOCKED_OUT,
            outcome=audit_constants.AuditOutcome.FAILURE,
            object_type="accounts.User",
            object_repr=username[:255],
            message=(
                f"Temporarily locked after repeated failed attempts within "
                f"{int(_lockout_window().total_seconds())} seconds"
            ),
        )


def register_logout(*, user: User) -> None:
    audit_services.record(action=audit_constants.USER_LOGGED_OUT, actor=user, obj=user)


def set_user_roles(*, actor: Actor, user: User, roles: list[str], reason: str = "") -> User:
    """Replace a user's role membership.

    Authorization runs **before** the transaction opens, not inside it. This matters:
    ``policy.require`` records the denial to the audit trail and then raises, and if that
    write happened inside the unit of work the rollback would take the audit record with
    it — leaving no trace of exactly the event specification section 18 most wants
    recorded.

    So the shape of every service function is: authorise, then transact.
    """
    policy.require(actor, MANAGE_USERS, reason=reason)
    return _apply_user_roles(actor=actor, user=user, roles=roles, reason=reason)


@transaction.atomic
def _apply_user_roles(*, actor: Actor, user: User, roles: list[str], reason: str) -> User:
    """Perform the role change and record it, as one unit of work."""
    valid = set(Role.values)
    unknown = sorted(set(roles) - valid)
    if unknown:
        raise ValueError(f"Unknown roles: {', '.join(unknown)}")

    before = {"roles": sorted(user.role_names)}

    groups = Group.objects.filter(name__in=roles)
    user.groups.set(groups)

    after = {"roles": sorted(user.role_names)}

    audit_services.record(
        action=audit_constants.USER_ROLES_CHANGED,
        actor=actor,
        obj=user,
        before=before,
        after=after,
        change_reason=reason,
        message=f"Roles set to: {', '.join(after['roles']) or 'none'}",
    )
    return user
