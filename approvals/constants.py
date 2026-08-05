"""What an approver may decide, and what the trail calls it. §15.2, §18."""

from __future__ import annotations

from django.db import models


class Decision(models.TextChoices):
    """Two outcomes, and deliberately no third.

    §15.2 gives `PENDING_APPROVAL` exactly two exits: `ON_AIR` and back to `PLANNED`. A
    "returned for changes" or "deferred" state would be a new node in the graph, and the graph
    is the specification's, not ours.
    """

    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


VIEW_APPROVALS = "approvals.view_approvaldecision"

APPROVAL_RECORDED = "APPROVAL_DECISION_RECORDED"
APPROVAL_REFUSED = "APPROVAL_REFUSED"
