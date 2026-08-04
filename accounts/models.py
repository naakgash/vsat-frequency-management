"""User, role membership and login attempt records."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from accounts.constants import Role


class User(AbstractUser):
    """Application user.

    A custom model from the first migration, even though it adds little today:
    swapping ``AUTH_USER_MODEL`` after operational data exists is one of the few
    genuinely painful migrations in Django, and this project will need per-user scope
    grants, MFA enrolment (section 21.6) and possibly an LDAP identifier (OQ-16).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Unique and required, unlike Django's default: account recovery and operational
    # notification both need a reliable address.
    email = models.EmailField(unique=True)

    full_name = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=200, blank=True)

    # Set when an administrator deactivates an account. Users are never deleted
    # (specification section 20) — their audit history must remain attributable.
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractUser.Meta):  # type: ignore[name-defined,misc]
        db_table = "accounts_user"
        permissions = [
            ("manage_users", "Can create and edit users and assign roles"),
            ("manage_scopes", "Can grant and revoke Beam, Hub and Gateway scope"),
        ]

    def __str__(self) -> str:
        return self.get_username()

    def get_absolute_url(self) -> str:
        # Imported here rather than at module scope: models are loaded before the URL
        # configuration, and a top-level import would create a circular dependency.
        from django.urls import reverse

        return reverse("administration:user-detail", kwargs={"user_id": self.pk})

    # --- Roles ---------------------------------------------------------------
    @property
    def role_names(self) -> frozenset[str]:
        """Role group names held by this user.

        Not cached on the instance: a role change during a request must take effect
        immediately, and the query is a single indexed join.
        """
        return frozenset(self.groups.values_list("name", flat=True))

    def has_role(self, role: str) -> bool:
        return role in self.role_names

    @property
    def is_admin(self) -> bool:
        """True for the application Admin role.

        Deliberately distinct from ``is_superuser``. A Django superuser is a database
        escape hatch for emergency console use; the Admin role is a business role. Scope
        bypass keys off this property, so that granting someone the Admin role is a
        visible, audited act rather than a hidden flag.
        """
        return self.has_role(Role.ADMIN)

    @property
    def display_roles(self) -> list[str]:
        held = self.role_names
        return [Role(name).label for name in Role.values if name in held]


class ScopeGrant(models.Model):
    """Base for object-level authorization grants (specification section 6).

    The foreign key on each subclass is declared as a **string reference** to a model in
    ``inventory``. That is deliberate and load-bearing: it gives full referential
    integrity without ``accounts`` importing a domain module, which is what keeps the
    ``accounts does not import domain modules`` contract intact. The resolvers that
    interpret these grants live in ``inventory``, which may import ``accounts``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True


class UserGatewayScope(ScopeGrant):
    """Authorises a user for a Gateway, and — per A-17 — for its Hubs.

    The cascade is the point: granting a teleport site should not require listing every
    hub at it, and a hub added later should be covered without a second grant. OQ-30
    confirms this reading.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="gateway_scopes"
    )
    gateway = models.ForeignKey(
        "inventory.Gateway", on_delete=models.PROTECT, related_name="user_scopes"
    )

    class Meta:
        db_table = "user_gateway_scope"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["user", "gateway"], name="uq_user_gateway_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> gateway {self.gateway_id}"


class UserBeamScope(ScopeGrant):
    """Authorises a user to act on one Beam. **A-17**, §25.

    Planned in S2 alongside the Gateway and Hub grants and deferred until the Beam existed to
    point at. S10 is where it becomes load-bearing: §25 says an *"Operator can create Satnet
    only under authorized Beam"*, and until this table existed that sentence had nothing to
    check against.

    **A grant governs acting, not looking.** Every authenticated user can still read every
    Beam — an operator has to be able to see what is out there before asking for access to it,
    and a Beam list that silently hid most of the fleet would look like missing data rather
    than like a permissions boundary. Narrowing *reads* is a separate decision with its own
    consequences for the S11 wizard, and it is not made here.

    No cascade in either direction. A Beam grant says nothing about Hubs and a Hub grant
    nothing about Beams: scope is conjunctive (**A-17**, **OQ-30**), so a Satnet needs both,
    and inferring one from the other would quietly satisfy half the requirement.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="beam_scopes")
    beam = models.ForeignKey("beams.Beam", on_delete=models.PROTECT, related_name="user_scopes")

    class Meta:
        db_table = "user_beam_scope"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["user", "beam"], name="uq_user_beam_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> beam {self.beam_id}"


class UserHubScope(ScopeGrant):
    """Authorises a user for one Hub.

    Does **not** imply the parent Gateway: a hub-level grant is narrower than a site-level
    one, and widening it silently would hand out access nobody granted (A-17).
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="hub_scopes")
    hub = models.ForeignKey("inventory.Hub", on_delete=models.PROTECT, related_name="user_scopes")

    class Meta:
        db_table = "user_hub_scope"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["user", "hub"], name="uq_user_hub_scope"),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> hub {self.hub_id}"


class LoginAttempt(models.Model):
    """Record of one authentication attempt, successful or not.

    Backs the rate limiting and temporary lockout of specification sections 21.4 and
    21.5. Kept separate from ``AuditEvent`` because it is queried on a hot path — every
    login reads it — and because it is prunable operational data, whereas audit is not.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Text, not a foreign key: failed attempts against a username that does not exist are
    # exactly the ones worth counting.
    username = models.CharField(max_length=150, db_index=True)
    successful = models.BooleanField()
    source_ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.CharField(max_length=512, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "accounts_login_attempt"
        ordering = ["-occurred_at"]
        default_permissions = ()
        indexes = [
            # The lockout query: recent failures for a username or an address.
            models.Index(
                fields=["username", "-occurred_at"],
                name="login_username_recent_idx",
                condition=models.Q(successful=False),
            ),
            models.Index(
                fields=["source_ip", "-occurred_at"],
                name="login_ip_recent_idx",
                condition=models.Q(successful=False),
            ),
        ]

    def __str__(self) -> str:
        outcome = "success" if self.successful else "failure"
        return f"{self.username} {outcome} at {self.occurred_at:%Y-%m-%d %H:%M:%S}"

    @classmethod
    def recent_failures_for_username(cls, *, username: str, window: timedelta) -> int:
        """Count recent failed attempts against one username, from any address.

        Counting per username rather than per session means an attacker cannot evade the
        limit by rotating source addresses against a single account.
        """
        return cls.objects.filter(
            username=username,
            successful=False,
            occurred_at__gte=timezone.now() - window,
        ).count()

    @classmethod
    def recent_failures_for_ip(cls, *, source_ip: str | None, window: timedelta) -> int:
        """Count recent failed attempts from one address, against any username.

        Catches password spraying, where each individual account stays below its own
        threshold. This is counted separately from the per-username total and carries a
        much higher limit on purpose: operators commonly share a source address behind
        NAT or a VPN concentrator, so a limit low enough to be useful per account would
        lock out an entire site the moment one person fat-fingered their password.
        """
        if not source_ip:
            return 0
        return cls.objects.filter(
            source_ip=source_ip,
            successful=False,
            occurred_at__gte=timezone.now() - window,
        ).count()
