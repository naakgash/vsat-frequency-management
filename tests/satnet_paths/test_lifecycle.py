"""The §15.2 transition graph, and what each move does to the spectrum. §15.3, §15.5.

Three things these tests are really about.

**A status change is a spectrum change.** Planning a draft writes occupancy rows and can be
refused by the exclusion constraint; retiring releases them. The reservation table is not a
mirror of the status column that something reconciles later.

**The graph is closed.** Every move that exists is here, and a representative illegal one is
too — because a transition system that only tests its happy paths is one that quietly accepts
`CANCELLED → ON_AIR` the first time somebody adds a button.

**§12's separation of duties is real.** An Operator plans and submits; an Approver decides,
suspends and retires. Neither can do the other's job, and an administrator does not inherit
the approval authority — `docs/design/03` §2.1 marks those rows "—" for admin deliberately.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from accounts.constants import Role
from satnet_paths import lifecycle
from satnet_paths.constants import PathStatus
from spectrum.models import SpectrumReservation
from tests.factories import make_user

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


def _rows(path):
    return SpectrumReservation.objects.filter(satnet_path_id=path.pk)


# ---------------------------------------------------------------------------
# The graph itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "actions"),
    [
        (PathStatus.DRAFT, {"plan", "cancel"}),
        (PathStatus.PLANNED, {"submit", "cancel"}),
        (PathStatus.PENDING_APPROVAL, {"approve", "reject"}),
        (PathStatus.ON_AIR, {"suspend", "retire"}),
        (PathStatus.SUSPENDED, {"resume", "retire"}),
    ],
)
def test_the_graph_offers_exactly_what_section_15_2_allows(status, actions):
    assert {t.action for t in lifecycle.available_from(status)} == actions


@pytest.mark.parametrize("status", [PathStatus.RETIRED, PathStatus.CANCELLED])
def test_a_final_status_offers_nothing(status):
    """History does not move. A `RETIRED` allocation is replaced by a new one, never revived —
    reviving it would give one revision two live periods."""
    assert lifecycle.available_from(status) == ()


def test_an_illegal_move_is_refused_by_name():
    with pytest.raises(lifecycle.IllegalTransition, match="cannot be approve"):
        lifecycle.find(PathStatus.DRAFT, "approve")


def test_the_refusal_says_what_is_possible_instead():
    """A dead end without a direction is the failure mode §9.5 objects to elsewhere, and it is
    no better here."""
    with pytest.raises(lifecycle.IllegalTransition, match="plan, cancel"):
        lifecycle.find(PathStatus.DRAFT, "retire")


# ---------------------------------------------------------------------------
# What each move does to the spectrum
# ---------------------------------------------------------------------------
def test_planning_a_draft_writes_its_occupancy_rows(lifecycle_world, make_path):
    """A draft holds nothing (**A-12**), so the transition out of it is the first write."""
    path = make_path(PathStatus.DRAFT)
    assert not _rows(path).exists()

    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="plan")

    assert _rows(path).count() == 2
    assert all(row.reserves_spectrum for row in _rows(path))


def test_submitting_moves_the_rows_rather_than_rewriting_them(lifecycle_world, make_path):
    """The occupancy rows carry the status too, because the exclusion constraint's predicate
    has to be evaluable from one row. Both columns move together or the CHECK refuses."""
    path = make_path(PathStatus.PLANNED)
    before = set(_rows(path).values_list("id", flat=True))

    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="submit")

    assert set(_rows(path).values_list("id", flat=True)) == before
    assert {row.status for row in _rows(path)} == {PathStatus.PENDING_APPROVAL}


def test_retiring_releases_the_spectrum(lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)

    lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="retire")

    assert not _rows(path).exists()
    assert path.status == PathStatus.RETIRED


def test_cancelling_a_draft_releases_nothing_because_it_held_nothing(lifecycle_world, make_path):
    path = make_path(PathStatus.DRAFT)

    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="cancel")

    assert path.status == PathStatus.CANCELLED
    assert not _rows(path).exists()


def test_the_spectrum_freed_by_a_retirement_can_be_taken(lifecycle_world, make_path):
    """The point of releasing, stated as behaviour rather than as a row count."""
    first = make_path(PathStatus.ON_AIR, code="LC-A")
    lifecycle.transition(actor=lifecycle_world["approver"], path=first, action="retire")

    second = make_path(PathStatus.PLANNED, code="LC-B")

    assert second.status == PathStatus.PLANNED
    assert _rows(second).count() == 2


# ---------------------------------------------------------------------------
# §15.3 / OQ-08 — what a suspension holds
# ---------------------------------------------------------------------------
@override_settings(SUSPENDED_RETAINS_SPECTRUM=True)
def test_a_suspension_keeps_its_spectrum_under_the_default_policy(lifecycle_world, make_path):
    """ADR-0017's default, and §15.3's recommendation: retaining is the safer error, because
    releasing means a suspension can silently become unresumable."""
    path = make_path(PathStatus.ON_AIR)

    lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="suspend")

    assert path.status == PathStatus.SUSPENDED
    assert all(row.reserves_spectrum for row in _rows(path))


@override_settings(SUSPENDED_RETAINS_SPECTRUM=False)
def test_a_suspension_releases_its_spectrum_under_the_other_policy(lifecycle_world, make_path):
    """The same code path with the setting flipped — which is the whole reason
    ``reserves_spectrum`` is a stored column and not a predicate on ``status`` (**A-12**)."""
    path = make_path(PathStatus.ON_AIR)

    lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="suspend")

    assert path.status == PathStatus.SUSPENDED
    assert not any(row.reserves_spectrum for row in _rows(path))


@override_settings(SUSPENDED_RETAINS_SPECTRUM=False)
def test_resuming_is_refused_when_the_freed_spectrum_was_taken(lifecycle_world, make_path):
    """The case that makes "release on suspend" the riskier policy, made concrete.

    The suspension gave the spectrum up, somebody else took it, and the resume now collides.
    That is the constraint working — and it must arrive as a message about somebody else's
    transmission, not as an unhandled integrity error.
    """
    suspended = make_path(PathStatus.ON_AIR, code="LC-S")
    lifecycle.transition(actor=lifecycle_world["approver"], path=suspended, action="suspend")
    make_path(PathStatus.PLANNED, code="LC-T")  # same centre, now free

    with pytest.raises(lifecycle.TransitionRefused, match="already reserved"):
        lifecycle.transition(actor=lifecycle_world["approver"], path=suspended, action="resume")


# ---------------------------------------------------------------------------
# §12 — who may do what
# ---------------------------------------------------------------------------
def test_an_operator_cannot_suspend_or_retire(lifecycle_world, make_path):
    from accounts.policy import PermissionDenied

    path = make_path(PathStatus.ON_AIR)

    with pytest.raises(PermissionDenied):
        lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="suspend")


def test_an_approver_cannot_plan(lifecycle_world, make_path):
    from accounts.policy import PermissionDenied

    path = make_path(PathStatus.DRAFT)

    with pytest.raises(PermissionDenied):
        lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="plan")


def test_an_administrator_does_not_inherit_the_approval_authority(lifecycle_world, make_path):
    """`docs/design/03` §2.1 marks the decision rows "—" for admin, and means it.

    An administrator who has to approve something is given the Approver role — a grant somebody
    can see and revoke — rather than the authority arriving invisibly with the job title.
    """
    from accounts.policy import PermissionDenied

    path = make_path(PathStatus.ON_AIR)

    with pytest.raises(PermissionDenied):
        lifecycle.transition(actor=lifecycle_world["admin"], path=path, action="retire")


def test_a_transition_needs_object_scope_as_well_as_the_capability(lifecycle_world, make_path):
    """**A-17** reaches the lifecycle: holding `suspend_satnetpath` is not enough."""
    path = make_path(PathStatus.ON_AIR)
    stranger = make_user("lc-stranger", roles=[Role.APPROVER])

    with pytest.raises(lifecycle.TransitionRefused):
        lifecycle.transition(actor=stranger, path=path, action="suspend")


def test_the_offered_moves_are_the_ones_this_actor_can_actually_make(lifecycle_world, make_path):
    """What the buttons come from. Offering a button that returns 403 reads as a fault rather
    than as a permission boundary."""
    path = make_path(PathStatus.ON_AIR)

    operator_sees = {t.action for t in lifecycle.offered_to(lifecycle_world["operator"], path)}
    approver_sees = {t.action for t in lifecycle.offered_to(lifecycle_world["approver"], path)}

    assert operator_sees == set()
    assert approver_sees == {"suspend", "retire"}


def test_an_actor_without_scope_is_offered_nothing(lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)
    stranger = make_user("lc-nobody", roles=[Role.APPROVER])

    assert lifecycle.offered_to(stranger, path) == []


# ---------------------------------------------------------------------------
# §15.5 — optimistic locking on a button, not only on a form
# ---------------------------------------------------------------------------
def test_a_move_computed_against_a_stale_page_is_refused(lifecycle_world, make_path):
    """A button pressed on a page rendered ten minutes ago is the same problem as a stale
    form: somebody else has already moved the allocation, and the graph would happily accept
    the move from wherever it is now."""
    path = make_path(PathStatus.ON_AIR)
    stale_version = path.record_version
    lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="suspend")

    with pytest.raises(lifecycle.StaleRecordError):
        lifecycle.transition(
            actor=lifecycle_world["approver"],
            path=path,
            action="retire",
            expected_version=stale_version,
        )


def test_a_move_with_no_stated_version_is_accepted(lifecycle_world, make_path):
    """Absent means "no opinion", which is what a service call from a script has. Requiring one
    would make the service unusable by the importer that S15 will build."""
    path = make_path(PathStatus.DRAFT)

    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="plan")

    assert path.status == PathStatus.PLANNED


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------
def test_approval_cannot_be_reached_through_the_plain_transition_service(
    lifecycle_world, make_path
):
    """§18. A decision that skipped ``approvals.services`` would move the allocation on air and
    leave no Approval Decision behind it — the trail would show a status change with nobody
    attached to it."""
    path = make_path(PathStatus.PENDING_APPROVAL)

    with pytest.raises(lifecycle.TransitionRefused, match="approvals service"):
        lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="approve")

    assert path.status == PathStatus.PENDING_APPROVAL
