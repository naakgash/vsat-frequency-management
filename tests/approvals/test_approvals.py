"""Second-person approval, and the record it leaves. §12, §15.2, §18, §26.14.

The rule this file exists for is one sentence — *the person who planned a transmission may not
be the person who puts it on air* — and almost everything here is a way of checking that the
sentence cannot be walked around: not by an administrator, not by pressing the button twice,
not by reaching the transition service directly, and not by editing the decision afterwards.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse

from accounts.constants import Role
from accounts.models import UserBeamScope, UserHubScope
from approvals import services
from approvals.constants import Decision
from approvals.models import ApprovalDecision
from satnet_paths import lifecycle
from satnet_paths.constants import PathStatus
from spectrum.models import SpectrumReservation
from tests.factories import make_user

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------
def test_approval_moves_the_allocation_and_records_who_decided(lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)

    decision = services.decide(
        actor=lifecycle_world["approver"], path=path, decision=Decision.APPROVED, comment="Fine."
    )

    path.refresh_from_db()
    assert path.status == PathStatus.ON_AIR
    assert decision.decided_by == lifecycle_world["approver"]
    assert decision.from_status == PathStatus.PENDING_APPROVAL
    assert decision.to_status == PathStatus.ON_AIR
    assert {row.status for row in SpectrumReservation.objects.filter(satnet_path_id=path.pk)} == {
        PathStatus.ON_AIR
    }


def test_rejection_sends_it_back_to_planned_and_keeps_the_spectrum(lifecycle_world, make_path):
    """§15.2 sends a rejection to `PLANNED`, which still holds its spectrum (**A-12**).

    That is the right behaviour and worth pinning: a rejection is "not yet", and releasing the
    frequency would mean the operator loses the slot for a correction they were asked to make.
    """
    path = make_path(PathStatus.PENDING_APPROVAL)

    services.decide(
        actor=lifecycle_world["approver"], path=path, decision=Decision.REJECTED, comment="No."
    )

    path.refresh_from_db()
    assert path.status == PathStatus.PLANNED
    assert all(
        row.reserves_spectrum for row in SpectrumReservation.objects.filter(satnet_path_id=path.pk)
    )


def test_a_rejection_stays_visible_after_a_later_approval(lifecycle_world, make_path):
    """Append-only means the trail keeps the argument, not just its conclusion (§18)."""
    path = make_path(PathStatus.PENDING_APPROVAL)
    services.decide(actor=lifecycle_world["approver"], path=path, decision=Decision.REJECTED)
    path.refresh_from_db()
    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="submit")
    services.decide(actor=lifecycle_world["approver"], path=path, decision=Decision.APPROVED)

    assert list(
        ApprovalDecision.objects.filter(satnet_path=path)
        .order_by("decided_at")
        .values_list("decision", flat=True)
    ) == [Decision.REJECTED, Decision.APPROVED]


# ---------------------------------------------------------------------------
# OQ-11 — the second person
# ---------------------------------------------------------------------------
def test_the_author_may_not_approve_their_own_allocation(lifecycle_world, make_path):
    """**OQ-11**, §12. The Approver role is decorative if this does not hold."""
    both = make_user("lc-both", roles=[Role.OPERATOR, Role.APPROVER])
    UserBeamScope.objects.create(user=both, beam=lifecycle_world["setup"].beam)
    UserHubScope.objects.create(user=both, hub=lifecycle_world["hub"])
    path = make_path(PathStatus.DRAFT, actor=both)
    lifecycle.transition(actor=both, path=path, action="plan")
    lifecycle.transition(actor=both, path=path, action="submit")

    with pytest.raises(services.ApprovalRefused, match="may not also approve"):
        services.decide(actor=both, path=path, decision=Decision.APPROVED)

    path.refresh_from_db()
    assert path.status == PathStatus.PENDING_APPROVAL
    assert not ApprovalDecision.objects.exists()


@override_settings(REQUIRE_SEPARATE_APPROVER=False)
def test_the_rule_can_be_turned_off_because_it_is_an_open_question(lifecycle_world, make_path):
    """**OQ-11** is not settled by the specification, so the platform holds a position rather
    than a rule: default on, and one setting away from off."""
    both = make_user("lc-both-off", roles=[Role.OPERATOR, Role.APPROVER])
    UserBeamScope.objects.create(user=both, beam=lifecycle_world["setup"].beam)
    UserHubScope.objects.create(user=both, hub=lifecycle_world["hub"])
    path = make_path(PathStatus.DRAFT, actor=both)
    lifecycle.transition(actor=both, path=path, action="plan")
    lifecycle.transition(actor=both, path=path, action="submit")

    services.decide(actor=both, path=path, decision=Decision.APPROVED)

    path.refresh_from_db()
    assert path.status == PathStatus.ON_AIR


def test_a_refused_self_approval_records_nothing(lifecycle_world, make_path):
    """A decision row for a move that did not happen is worse than no row: it says the
    allocation went on air when it did not."""
    both = make_user("lc-both-2", roles=[Role.OPERATOR, Role.APPROVER])
    UserBeamScope.objects.create(user=both, beam=lifecycle_world["setup"].beam)
    UserHubScope.objects.create(user=both, hub=lifecycle_world["hub"])
    path = make_path(PathStatus.DRAFT, actor=both)
    lifecycle.transition(actor=both, path=path, action="plan")
    lifecycle.transition(actor=both, path=path, action="submit")

    with pytest.raises(services.ApprovalRefused):
        services.decide(actor=both, path=path, decision=Decision.APPROVED)

    assert ApprovalDecision.objects.count() == 0


# ---------------------------------------------------------------------------
# Who and what
# ---------------------------------------------------------------------------
def test_an_operator_cannot_approve(lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)

    with pytest.raises(PermissionDenied):
        services.decide(actor=lifecycle_world["operator"], path=path, decision=Decision.APPROVED)


def test_only_a_pending_allocation_can_be_decided_on(lifecycle_world, make_path):
    path = make_path(PathStatus.PLANNED)

    with pytest.raises(services.ApprovalRefused, match="not awaiting approval"):
        services.decide(actor=lifecycle_world["approver"], path=path, decision=Decision.APPROVED)


def test_there_is_no_third_outcome(lifecycle_world, make_path):
    """§15.2 gives `PENDING_APPROVAL` two exits. "Returned for changes" would be a new node in
    the graph, and the graph is the specification's."""
    path = make_path(PathStatus.PENDING_APPROVAL)

    with pytest.raises(services.ApprovalRefused, match="not a decision"):
        services.decide(actor=lifecycle_world["approver"], path=path, decision="DEFERRED")


# ---------------------------------------------------------------------------
# §18 — the record is evidence
# ---------------------------------------------------------------------------
def test_a_decision_cannot_be_edited(lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)
    decision = services.decide(
        actor=lifecycle_world["approver"], path=path, decision=Decision.APPROVED
    )

    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        ApprovalDecision.objects.filter(pk=decision.pk).update(comment="actually, no")


def test_a_decision_cannot_be_deleted(lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)
    services.decide(actor=lifecycle_world["approver"], path=path, decision=Decision.APPROVED)

    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        ApprovalDecision.objects.all().delete()


def test_the_database_refuses_a_decision_that_disagrees_with_the_graph(lifecycle_world, make_path):
    """A row claiming to have approved something into `SUSPENDED` would make the trail
    disagree with §15.2, and the trail is what an audit reads."""
    path = make_path(PathStatus.PENDING_APPROVAL)

    with pytest.raises(IntegrityError, match="ck_approval_decision_transition"):
        with transaction.atomic():
            ApprovalDecision.objects.create(
                satnet_path=path,
                decision=Decision.APPROVED,
                decided_by=lifecycle_world["approver"],
                from_status=PathStatus.PENDING_APPROVAL,
                to_status=PathStatus.SUSPENDED,
            )


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
def test_the_queue_shows_what_is_waiting(client, lifecycle_world, make_path):
    make_path(PathStatus.PENDING_APPROVAL, code="LC-Q")
    client.force_login(lifecycle_world["approver"])

    response = client.get(reverse("approvals:queue"))

    assert response.status_code == 200
    assert "LC-Q" in response.content.decode()


def test_the_queue_does_not_show_what_is_not_waiting(client, lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="LC-NOTQ")
    client.force_login(lifecycle_world["approver"])

    response = client.get(reverse("approvals:queue"))

    assert "LC-NOTQ" not in response.content.decode()


def test_an_operator_may_read_the_queue(client, lifecycle_world, make_path):
    """Hiding it from the person who submitted turns "where is my allocation" into a question
    for somebody else."""
    make_path(PathStatus.PENDING_APPROVAL)
    client.force_login(lifecycle_world["operator"])

    assert client.get(reverse("approvals:queue")).status_code == 200


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
def test_approving_over_http_moves_the_allocation(client, lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)
    client.force_login(lifecycle_world["approver"])

    client.post(
        reverse("approvals:decide", kwargs={"pk": path.pk, "outcome": "approve"}),
        {"comment": "Checked against the plan.", "record_version": path.record_version},
    )

    path.refresh_from_db()
    assert path.status == PathStatus.ON_AIR


def test_an_operator_posting_an_approval_is_refused(client, lifecycle_world, make_path):
    path = make_path(PathStatus.PENDING_APPROVAL)
    client.force_login(lifecycle_world["operator"])

    response = client.post(
        reverse("approvals:decide", kwargs={"pk": path.pk, "outcome": "approve"}), {}
    )

    assert response.status_code == 403
    path.refresh_from_db()
    assert path.status == PathStatus.PENDING_APPROVAL


def test_a_self_approval_over_http_returns_409(client, lifecycle_world, make_path):
    both = make_user("lc-both-http", roles=[Role.OPERATOR, Role.APPROVER])
    UserBeamScope.objects.create(user=both, beam=lifecycle_world["setup"].beam)
    UserHubScope.objects.create(user=both, hub=lifecycle_world["hub"])
    path = make_path(PathStatus.DRAFT, actor=both)
    lifecycle.transition(actor=both, path=path, action="plan")
    lifecycle.transition(actor=both, path=path, action="submit")
    client.force_login(both)

    response = client.post(
        reverse("approvals:decide", kwargs={"pk": path.pk, "outcome": "approve"}), {}
    )

    assert response.status_code == 409
    assert "may not also approve" in response.content.decode()
