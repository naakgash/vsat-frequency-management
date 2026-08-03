"""Append-only audit trail.

Specification section 18 and acceptance criterion 26.17. Audit records cannot be edited
or deleted through the application — and, because "through the application" is not a
guarantee, a database trigger enforces the same rule against direct SQL. See
``0002_audit_event_immutable``.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from audit.constants import AuditOutcome


class AuditEvent(models.Model):
    """One recorded action. Immutable once written.

    The model deliberately has no ``save()`` override guarding updates: a Python-level
    guard would be bypassed by ``queryset.update()``, raw SQL or a future maintenance
    script. The guarantee lives in the database.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # --- Actor ---------------------------------------------------------------
    # PROTECT rather than CASCADE: deleting a user must never delete their history.
    # Users are deactivated, not deleted (specification section 20).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_events",
        help_text="Null for anonymous or system-initiated actions.",
    )
    # Denormalised so the record stays readable without a join, and stays truthful even
    # if the account is later renamed.
    actor_username = models.CharField(max_length=150, blank=True)

    # --- What happened -------------------------------------------------------
    action = models.CharField(max_length=64, help_text="Stable action code, e.g. USER_LOGGED_IN.")
    outcome = models.CharField(max_length=8, choices=AuditOutcome.choices)
    message = models.TextField(blank=True)

    # --- Which object --------------------------------------------------------
    # Stored as a label plus a UUID rather than a real foreign key or a generic
    # relation: audit rows outlive the objects they describe, and a foreign key would
    # either block deletion of legitimately removable rows or cascade away the history.
    object_type = models.CharField(
        max_length=100, blank=True, help_text="app_label.ModelName of the affected object."
    )
    object_id = models.UUIDField(null=True, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)

    # --- Field-level difference (section 18: before/after values) ------------
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    change_reason = models.TextField(blank=True)

    # --- Request context -----------------------------------------------------
    request_id = models.UUIDField(null=True, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    # --- Import provenance (section 18: import source) -----------------------
    # Plain UUID, not a foreign key: imports_exports lands in S15, and audit must not
    # depend on a module above it in the dependency direction (docs/design/01).
    import_batch_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "audit_event"
        verbose_name = "Audit event"
        verbose_name_plural = "Audit events"
        ordering = ["-occurred_at"]
        # Only viewing is a permission. There is deliberately no add/change/delete
        # permission for any role, including admin (docs/design/03 section 2.1).
        default_permissions = ()
        permissions = [
            ("view_auditevent", "Can view their own audit events"),
            ("view_all_auditevent", "Can view all audit events"),
        ]
        indexes = [
            models.Index(fields=["-occurred_at"], name="audit_occurred_desc_idx"),
            models.Index(
                fields=["object_type", "object_id", "-occurred_at"],
                name="audit_object_history_idx",
            ),
            models.Index(fields=["actor", "-occurred_at"], name="audit_actor_idx"),
            models.Index(fields=["action", "-occurred_at"], name="audit_action_idx"),
            models.Index(
                fields=["-occurred_at"],
                name="audit_failures_idx",
                condition=models.Q(outcome=AuditOutcome.FAILURE),
            ),
            # Field-level difference search (section 18).
            GinIndex(fields=["before"], name="audit_before_gin_idx"),
            GinIndex(fields=["after"], name="audit_after_gin_idx"),
        ]

    def __str__(self) -> str:
        who = self.actor_username or "anonymous"
        return f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {who} {self.action} ({self.outcome})"
