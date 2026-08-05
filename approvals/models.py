"""The record of a decision. §15.2, §18, `docs/design/02` §8.

One row per decision, append-only, and never the *source* of the allocation's status — the
Satnet Path's own ``status`` column is that. This is the answer to "who decided, when, and what
did they say", which §18 requires and which a status column cannot hold: it keeps one value,
and an allocation may be rejected twice before it is approved.

Append-only is enforced by a trigger, like ``audit_event`` and for the same reason (**A-15**):
"there is no edit screen" is not a guarantee when ``queryset.update()``, a maintenance script
and a psql session all exist.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from approvals.constants import Decision
from spectrum.constants import ReservationStatus


class ApprovalDecision(models.Model):
    """One approver's decision on one revision of one Satnet Path."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    satnet_path = models.ForeignKey(
        "satnet_paths.SatnetPath", on_delete=models.PROTECT, related_name="approval_decisions"
    )
    decision = models.CharField(max_length=8, choices=Decision.choices)

    decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="approval_decisions"
    )
    decided_at = models.DateTimeField(default=timezone.now)
    comment = models.TextField(blank=True)

    #: The transition this decision caused, stored rather than derived. The graph may gain a
    #: node; a decision recorded five years ago must still say what it actually did.
    from_status = models.CharField(max_length=16, choices=ReservationStatus.choices)
    to_status = models.CharField(max_length=16, choices=ReservationStatus.choices)

    class Meta:
        db_table = "approval_decision"
        ordering = ["-decided_at"]
        default_permissions = ("view",)
        constraints = [
            # Every decision leaves `PENDING_APPROVAL`, and each outcome has exactly one
            # destination (§15.2). A row claiming to have approved something into `SUSPENDED`
            # would make the trail disagree with the graph, and the trail is what an audit
            # reads.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        decision=Decision.APPROVED,
                        from_status=ReservationStatus.PENDING_APPROVAL,
                        to_status=ReservationStatus.ON_AIR,
                    )
                    | models.Q(
                        decision=Decision.REJECTED,
                        from_status=ReservationStatus.PENDING_APPROVAL,
                        to_status=ReservationStatus.PLANNED,
                    )
                ),
                name="ck_approval_decision_transition",
            ),
        ]
        indexes = [
            models.Index(fields=["satnet_path", "-decided_at"], name="approval_path_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.decision} by {self.decided_by} at {self.decided_at:%Y-%m-%d %H:%M} UTC"

    @property
    def is_approval(self) -> bool:
        return self.decision == Decision.APPROVED
