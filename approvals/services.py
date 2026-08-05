"""Deciding on an allocation. §12, §15.2, **OQ-11**.

This module exists because a decision is two things that must not come apart: a **status
change** and a **record of who made it**. `satnet_paths.lifecycle` owns the graph and the
spectrum; this owns the second person and the paper trail, and it refuses to let the graph be
walked past `PENDING_APPROVAL` by any other route (``on_behalf_of_approval``).

**The separate-approver rule is checked here and nowhere else.** §12 gives the Approver role
its purpose, and a platform where the same person may plan and approve their own transmission
has an Approver role in name only. It is a setting (`REQUIRE_SEPARATE_APPROVER`, **OQ-11**)
because the specification leaves it open, and the default is on.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction

from accounts import policy
from accounts.models import User
from accounts.types import Actor
from approvals.constants import APPROVAL_RECORDED, APPROVAL_REFUSED, Decision
from approvals.models import ApprovalDecision
from audit import services as audit_services
from satnet_paths import lifecycle
from satnet_paths.constants import APPROVE_SATNET_PATH, REJECT_SATNET_PATH, PathStatus
from satnet_paths.models import SatnetPath

#: Which capability each outcome needs, and which move it makes. `docs/design/03` §2.2 names
#: the two capabilities separately: approving and rejecting are not the same authority, and a
#: reviewer trusted to send something back is not thereby trusted to put it on air.
_OUTCOMES = {
    Decision.APPROVED: (APPROVE_SATNET_PATH, "approve"),
    Decision.REJECTED: (REJECT_SATNET_PATH, "reject"),
}


class ApprovalRefused(Exception):
    """The decision cannot be made — wrong status, wrong person, or a rule about the world."""


def decide(
    *,
    actor: Actor,
    path: SatnetPath,
    decision: str,
    comment: str = "",
    reason: str = "",
    expected_version: int | None = None,
) -> ApprovalDecision:
    """Record a decision and make the move it implies, in one transaction.

    If the transition is refused — most often because the spectrum was taken while the
    allocation sat in the queue — **nothing is recorded**. A decision row for a move that did
    not happen is worse than no row: it says the allocation went on air when it did not.
    """
    capability, action = _outcome_for(decision)
    policy.require(actor, capability, path, reason=reason)

    # ``policy.require`` has already refused an anonymous caller, so this is a narrowing rather
    # than a check — but the column is not nullable and the type has to say so. A decision with
    # nobody attached to it is the one thing this table must never hold (§18).
    if not isinstance(actor, User):
        raise ApprovalRefused("A decision has to be made by a signed-in person (§18).")

    if path.status != PathStatus.PENDING_APPROVAL:
        raise ApprovalRefused(
            f"{path.code} is {path.status}, not awaiting approval. Only an allocation in "
            f"{PathStatus.PENDING_APPROVAL} can be decided on."
        )
    _check_separate_approver(actor, path, capability)

    with transaction.atomic():
        from_status = path.status
        try:
            lifecycle.transition(
                actor=actor,
                path=path,
                action=action,
                reason=reason or comment,
                expected_version=expected_version,
                on_behalf_of_approval=True,
            )
        except lifecycle.TransitionRefused as exc:
            raise ApprovalRefused(str(exc)) from exc

        record = ApprovalDecision.objects.create(
            satnet_path=path,
            decision=decision,
            decided_by=actor,
            comment=comment,
            from_status=from_status,
            to_status=path.status,
        )

    audit_services.record(
        action=APPROVAL_RECORDED,
        actor=actor,
        obj=path,
        after={
            "decision": decision,
            "from_status": from_status,
            "to_status": path.status,
            "comment": comment,
        },
        change_reason=reason,
        message=f"{decision.title()} {path.code} revision {path.revision_number}",
    )
    return record


def _outcome_for(decision: str) -> tuple[str, str]:
    try:
        return _OUTCOMES[Decision(decision)]
    except ValueError as exc:
        raise ApprovalRefused(
            f"{decision!r} is not a decision. §15.2 gives PENDING_APPROVAL two exits: "
            f"{', '.join(Decision.values)}."
        ) from exc


def _check_separate_approver(actor: Actor, path: SatnetPath, capability: str) -> None:
    """**OQ-11**, §12. The author may not be the approver.

    Compares against ``created_by`` on the record rather than against the audit trail: the
    trail is the history of what happened, and a rule that has to search it in order to decide
    is a rule that fails differently once the trail is archived.

    An allocation with no author — which today means only the importer's rows (S15) — is not
    self-approvable by anybody, so the rule does not apply.
    """
    if not settings.REQUIRE_SEPARATE_APPROVER:
        return
    if path.created_by_id is None or not isinstance(actor, User):
        return
    if path.created_by_id != actor.pk:
        return

    policy.record_denial(actor, capability, path, detail="self-approval")
    audit_services.record(
        action=APPROVAL_REFUSED,
        actor=actor,
        obj=path,
        after={"rule": "REQUIRE_SEPARATE_APPROVER"},
        message=f"Refused self-approval of {path.code}",
    )
    raise ApprovalRefused(
        "You planned this Satnet Path, so you may not also approve it. A second person has to "
        "decide (§12). This requirement is the REQUIRE_SEPARATE_APPROVER setting."
    )


def pending_for(actor: Actor) -> Any:
    """The approval queue, scope-filtered. §10.3.

    Deliberately a selector-shaped function on the service module rather than its own module:
    there is one query, it exists only for the queue screen, and a `selectors.py` holding a
    single filter is a file to keep in step for no benefit.
    """
    from satnet_paths import selectors as path_selectors

    return path_selectors.current(actor).filter(status=PathStatus.PENDING_APPROVAL)
