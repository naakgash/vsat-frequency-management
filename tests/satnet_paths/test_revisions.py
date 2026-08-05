"""An `ON_AIR` allocation is never overwritten. §15.4, **A-14**, ADR-0014.

The ordering inside :func:`satnet_paths.lifecycle.revise` is the whole slice in one function,
and it is the kind of thing that looks like an implementation detail until it fails: the
predecessor's period closes and its spectrum is released **before** the successor is written,
because the exclusion constraint is `IMMEDIATE` and most revisions keep some of their own
frequency. Reverse the two statements and an allocation is refused by the row it is replacing.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from satnet_paths import lifecycle, services
from satnet_paths.constants import PathStatus
from satnet_paths.models import SatnetPath
from spectrum.models import SpectrumReservation

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


def _values(path, **extra):
    values = {
        "code": path.code,
        "direction": path.direction,
        "input_mode": path.input_mode,
        "input_value": path.input_value,
        "rolloff": path.rolloff,
        "guard_policy": path.guard_policy,
        "canonical_center_hz": path.canonical_center_hz,
        "valid_until": None,
    }
    values.update(extra)
    return values


# ---------------------------------------------------------------------------
# The ordering that makes it work
# ---------------------------------------------------------------------------
def test_a_revision_keeping_the_same_frequency_is_accepted(lifecycle_world, make_path):
    """The case the ordering exists for. A revision that changes only the bandwidth still
    overlaps its predecessor, and would be refused by it if the old rows were still there."""
    path = make_path(PathStatus.ON_AIR)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=12 * MHZ),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert successor.revision_number == 2
    assert successor.occupied_bw_hz == 12 * MHZ


def test_the_predecessor_is_closed_and_retired(lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)
    effective = timezone.now() + timezone.timedelta(days=1)

    lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=effective,
    )

    path.refresh_from_db()
    assert path.status == PathStatus.RETIRED
    assert path.valid_until == effective
    assert not SpectrumReservation.objects.filter(satnet_path_id=path.pk).exists()


def test_the_successor_starts_where_the_predecessor_ended(lifecycle_world, make_path):
    """Half-open periods (**A-10**): the old one ends exactly where the new one begins, and the
    two do not overlap. A gap or an overlap would both be wrong, in opposite ways."""
    path = make_path(PathStatus.ON_AIR)
    effective = timezone.now() + timezone.timedelta(days=1)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=effective,
    )

    path.refresh_from_db()
    assert successor.valid_from == path.valid_until


def test_the_chain_keeps_its_group_and_its_order(lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert successor.revision_group == path.revision_group
    assert successor.supersedes_id == path.pk
    assert [r.revision_number for r in lifecycle.revision_chain(successor)] == [1, 2]


def test_the_list_shows_only_the_successor(lifecycle_world, make_path):
    """§15.4. Older revisions are history; a list showing them all would show one allocation
    many times."""
    from satnet_paths import selectors

    path = make_path(PathStatus.ON_AIR)
    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert list(selectors.current(lifecycle_world["admin"])) == [successor]


# ---------------------------------------------------------------------------
# What a revision does not inherit
# ---------------------------------------------------------------------------
def test_approval_is_not_inherited(lifecycle_world, make_path):
    """An approved allocation's replacement is a different transmission, and §15.2 sends it
    back through the approver. Inheriting `ON_AIR` would let an operator change an approved
    frequency without anybody deciding on the change."""
    path = make_path(PathStatus.ON_AIR)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert successor.status == PathStatus.PENDING_APPROVAL


def test_a_planned_allocation_revises_into_a_planned_one(lifecycle_world, make_path):
    """Nothing approved it, so nothing has to re-approve it."""
    path = make_path(PathStatus.PLANNED)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert successor.status == PathStatus.PLANNED


def test_the_successor_is_recomputed_rather_than_copied(lifecycle_world, make_path):
    """Only the operator's inputs carry forward. A revision that copied the stored edges would
    carry an old Payload Path's arithmetic into an allocation validated against the current
    one."""
    path = make_path(PathStatus.ON_AIR)

    successor = lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, canonical_center_hz=70 * MHZ),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    assert successor.canonical_center_hz == 70 * MHZ
    assert successor.canonical_occupied_start_hz != path.canonical_occupied_start_hz
    assert successor.translated_occupied_start_hz != path.translated_occupied_start_hz


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_a_superseded_revision_cannot_be_revised_again(lifecycle_world, make_path):
    """Otherwise a chain forks, and ``uq_path_revision`` refuses the second branch with a
    constraint name instead of a sentence."""
    path = make_path(PathStatus.ON_AIR)
    lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path),
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )
    path.refresh_from_db()

    with pytest.raises(lifecycle.TransitionRefused, match="already been superseded"):
        lifecycle.revise(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path),
            change_effective_at=timezone.now() + timezone.timedelta(days=2),
        )


def test_a_cancelled_allocation_is_not_revised(lifecycle_world, make_path):
    path = make_path(PathStatus.DRAFT)
    lifecycle.transition(actor=lifecycle_world["operator"], path=path, action="cancel")

    with pytest.raises(lifecycle.TransitionRefused, match="history"):
        lifecycle.revise(actor=lifecycle_world["operator"], path=path, values=_values(path))


def test_a_revision_cannot_take_effect_before_the_allocation_began(lifecycle_world, make_path):
    """`ck_path_validity` would refuse the closed period anyway; this refuses it with a message
    that names the date somebody has to choose after."""
    path = make_path(PathStatus.ON_AIR)

    with pytest.raises(lifecycle.TransitionRefused, match="after"):
        lifecycle.revise(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path),
            change_effective_at=path.valid_from - timezone.timedelta(days=1),
        )


def test_an_approver_cannot_revise(lifecycle_world, make_path):
    """Revising is an authoring action, and §12 keeps authoring away from the approver."""
    path = make_path(PathStatus.ON_AIR)

    with pytest.raises(PermissionDenied):
        lifecycle.revise(
            actor=lifecycle_world["approver"],
            path=path,
            values=_values(path),
            change_effective_at=timezone.now() + timezone.timedelta(days=1),
        )


def test_a_refused_revision_leaves_the_original_alone(lifecycle_world, make_path):
    """One transaction (§15.6). A revision that closed the predecessor and then failed would
    leave an allocation retired and unreplaced — off air, with nobody told."""
    path = make_path(PathStatus.ON_AIR)
    blocker = make_path(PathStatus.PLANNED, code="LC-BLOCK", centre=80 * MHZ)

    with pytest.raises(services.PathBlockedError):
        lifecycle.revise(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path, canonical_center_hz=blocker.canonical_center_hz),
            change_effective_at=timezone.now() + timezone.timedelta(days=1),
        )

    path.refresh_from_db()
    assert path.status == PathStatus.ON_AIR
    assert SpectrumReservation.objects.filter(satnet_path_id=path.pk).count() == 2
    assert SatnetPath.objects.filter(revision_group=path.revision_group).count() == 1
