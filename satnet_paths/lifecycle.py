"""The §15.2 transition graph, and what each move does to the spectrum. §15.4, §15.5.

Three rules run through everything here.

**The graph is data, not branching.** :data:`TRANSITIONS` is the whole of what may follow
what, and every legality question is a dictionary lookup. A transition expressed as `if`
statements spread across views is one that disagrees with itself the first time somebody adds
a status — and §15.2's graph is exactly the kind of thing that gets an extra state.

**A status change is a spectrum change.** `PLANNED`, `PENDING_APPROVAL` and `ON_AIR` hold
their spectrum; `DRAFT`, `CANCELLED` and `RETIRED` do not; `SUSPENDED` depends on a setting
(**A-12**, **OQ-08**, ADR-0017). So planning a draft *writes* occupancy rows and can be
refused by the exclusion constraint, and retiring an allocation releases them. The reservation
table is not a mirror of the status column that some later job reconciles — it is written in
the same transaction, or the transition does not happen.

**An `ON_AIR` record is never overwritten** (§15.4). Changing one means :func:`revise`, which
closes the old period *before* opening the new one, in one transaction, because the exclusion
constraint is `IMMEDIATE` (**A-14**). ADR-0014.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts import policy
from accounts.types import Actor
from audit import services as audit_services
from satnet_paths.constants import (
    APPROVE_SATNET_PATH,
    CANCEL_SATNET_PATH,
    PATH_REVISED,
    PATH_STALE,
    PATH_TRANSITIONED,
    PLAN_SATNET_PATH,
    REJECT_SATNET_PATH,
    RETIRE_SATNET_PATH,
    REVISE_SATNET_PATH,
    SUBMIT_SATNET_PATH,
    SUSPEND_SATNET_PATH,
    PathStatus,
)
from satnet_paths.models import SatnetPath
from satnets import scope as satnet_scope
from spectrum import services as spectrum_services


@dataclasses.dataclass(frozen=True)
class Transition:
    """One legal move, and everything that decides whether it may be made."""

    action: str
    to_status: str
    capability: str
    label: str
    #: What happens to the occupancy rows. ``reserve`` writes them for the first time,
    #: ``restatus`` moves the existing ones, ``release`` drops them.
    spectrum: str

    @property
    def is_approval(self) -> bool:
        """Does this move need a *second person*? **OQ-11**, §12."""
        return self.action in {"approve", "reject"}


#: The §15.2 graph. Keyed by the status being left, because that is the question a screen
#: asks: *given where this allocation is, what may happen to it next?*
TRANSITIONS: dict[str, tuple[Transition, ...]] = {
    PathStatus.DRAFT: (
        Transition("plan", PathStatus.PLANNED, PLAN_SATNET_PATH, "Plan", "reserve"),
        Transition("cancel", PathStatus.CANCELLED, CANCEL_SATNET_PATH, "Cancel", "release"),
    ),
    PathStatus.PLANNED: (
        Transition("submit", PathStatus.PENDING_APPROVAL, SUBMIT_SATNET_PATH, "Submit", "restatus"),
        Transition("cancel", PathStatus.CANCELLED, CANCEL_SATNET_PATH, "Cancel", "release"),
    ),
    PathStatus.PENDING_APPROVAL: (
        Transition("approve", PathStatus.ON_AIR, APPROVE_SATNET_PATH, "Approve", "restatus"),
        Transition("reject", PathStatus.PLANNED, REJECT_SATNET_PATH, "Reject", "restatus"),
    ),
    PathStatus.ON_AIR: (
        Transition("suspend", PathStatus.SUSPENDED, SUSPEND_SATNET_PATH, "Suspend", "restatus"),
        Transition("retire", PathStatus.RETIRED, RETIRE_SATNET_PATH, "Retire", "release"),
    ),
    PathStatus.SUSPENDED: (
        Transition("resume", PathStatus.ON_AIR, SUSPEND_SATNET_PATH, "Resume", "restatus"),
        Transition("retire", PathStatus.RETIRED, RETIRE_SATNET_PATH, "Retire", "release"),
    ),
    # `RETIRED` and `CANCELLED` are terminal, and `IMPORT_REVIEW` has no transitions until the
    # importer exists to give it any (S15). Absent rather than empty-tupled: a missing key and
    # an empty tuple mean the same thing to `available_from`, and listing a status here with
    # nothing in it would read as an oversight.
}

#: Statuses whose fields may be edited in place. Everything else is changed by ``revise``
#: (`docs/design/03` §5), because an allocation that somebody has approved or that is on air
#: must not silently become a different allocation under the same identity.
EDITABLE_STATUSES = frozenset({PathStatus.DRAFT, PathStatus.PLANNED})


class IllegalTransition(Exception):
    """This move is not in the graph."""


class TransitionRefused(Exception):
    """The move is legal but the world says no — usually because the spectrum is taken.

    Separate from :class:`IllegalTransition` because the two need different screens: an
    illegal move is a bug or a stale button, and a refused one is a fact about somebody else's
    transmission that the operator has to act on.
    """


class NotEditable(Exception):
    """Field edits are not accepted in this status. §15.4."""


@dataclasses.dataclass(frozen=True)
class FieldChange:
    """One field that moved while a form was open. §15.5."""

    field: str
    yours: Any
    theirs: Any


class StaleRecordError(Exception):
    """Somebody else changed this record while the form was open. §15.5.

    Carries the *differences*, not just the fact. A bare "this record changed, reload"
    message makes an operator retype work they may not need to redo — and when the other
    edit touched a different field entirely, it makes them redo it for no reason.
    """

    def __init__(self, changes: list[FieldChange], current_version: int) -> None:
        self.changes = changes
        self.current_version = current_version
        super().__init__(
            f"This Satnet Path was changed by somebody else (it is now version "
            f"{current_version}). {len(changes)} field(s) differ."
        )


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------
def available_from(status: str) -> tuple[Transition, ...]:
    return TRANSITIONS.get(status, ())


def find(status: str, action: str) -> Transition:
    for transition in available_from(status):
        if transition.action == action:
            return transition
    raise IllegalTransition(
        f"A Satnet Path in {status} cannot be {action}ed. "
        f"From here it may only be: "
        f"{_actions_from(status)}."
    )


def _actions_from(status: str) -> str:
    """What to offer instead, for a refusal message. A dead end without a direction is the
    failure mode §9.5 objects to elsewhere, and it is no better here."""
    actions = ", ".join(t.action for t in available_from(status))
    return actions or "nothing — this is a final status"


def offered_to(actor: Actor, path: SatnetPath) -> list[Transition]:
    """The moves this actor may actually make on this record, for a screen to render.

    Capability *and* scope, because offering a button that returns 403 is worse than offering
    none: it reads as a system fault rather than as a permission boundary.
    """
    allowed, _ = satnet_scope.may_act_on(actor, beam_id=path.beam_id, hub_id=path.satnet.hub_id)
    if not allowed:
        return []
    return [
        transition
        for transition in available_from(path.status)
        if policy.allows(actor, transition.capability, path)
    ]


# ---------------------------------------------------------------------------
# Making a move
# ---------------------------------------------------------------------------
def transition(
    *,
    actor: Actor,
    path: SatnetPath,
    action: str,
    reason: str = "",
    expected_version: int | None = None,
    on_behalf_of_approval: bool = False,
) -> SatnetPath:
    """Move one allocation along the §15.2 graph, spectrum and all.

    ``on_behalf_of_approval`` is set only by ``approvals.services``, which has already run the
    second-person rule and recorded the decision. The flag exists so that this function refuses
    an approval reached any *other* way — a direct call from a view would skip the
    ``ApprovalDecision`` row that §18 requires the trail to contain.
    """
    move = find(path.status, action)
    policy.require(actor, move.capability, path, reason=reason)
    _require_scope(actor, path, move.capability)

    if move.is_approval and not on_behalf_of_approval:
        raise TransitionRefused(
            "Approvals are recorded through the approvals service so that every decision "
            "leaves an Approval Decision behind it (§18). This route would skip it."
        )

    _check_version(path, expected_version)
    return _apply(actor=actor, path=path, move=move, reason=reason)


@transaction.atomic
def _apply(*, actor: Actor, path: SatnetPath, move: Transition, reason: str) -> SatnetPath:
    before = {"status": path.status, "record_version": path.record_version}
    was = path.status

    path.status = move.to_status
    path.change_reason = reason or path.change_reason
    path.record_version += 1
    path.save(update_fields=["status", "change_reason", "record_version", "updated_at"])

    _move_spectrum(actor=actor, path=path, move=move, reason=reason)

    audit_services.record(
        action=PATH_TRANSITIONED,
        actor=actor,
        obj=path,
        before=before,
        after={"status": path.status, "record_version": path.record_version},
        change_reason=reason,
        message=f"{move.label}: {path.code} moved {was} → {path.status}",
    )
    return path


def _move_spectrum(*, actor: Actor, path: SatnetPath, move: Transition, reason: str) -> None:
    """Keep the occupancy rows in step with the status, in the same transaction.

    A `SpectrumConflictError` here is not an error in the usual sense — it is the constraint
    doing its job, most often when a suspended allocation is resumed after somebody else took
    the gap. It becomes a :class:`TransitionRefused` so the screen can say that rather than
    showing a traceback about a partial index.
    """
    try:
        if move.spectrum == "reserve":
            spectrum_services.reserve(
                actor=actor,
                occupancies=_occupancies_for(path),
                satnet_path_id=str(path.pk),
                direction=path.direction,
                status=path.status,
                valid_from=path.valid_from,
                valid_until=path.valid_until,
                suspended_retains=settings.SUSPENDED_RETAINS_SPECTRUM,
                reason=reason,
            )
        elif move.spectrum == "restatus":
            spectrum_services.set_status(
                actor=actor,
                satnet_path_id=str(path.pk),
                status=path.status,
                suspended_retains=settings.SUSPENDED_RETAINS_SPECTRUM,
                reason=reason,
            )
        elif move.spectrum == "release":
            spectrum_services.release(actor=actor, satnet_path_id=str(path.pk), reason=reason)
    except spectrum_services.SpectrumConflictError as exc:
        raise TransitionRefused(str(exc)) from exc
    except spectrum_services.OutsideEntitlementError as exc:
        raise TransitionRefused(str(exc)) from exc


def _occupancies_for(path: SatnetPath) -> list[spectrum_services.Occupancy]:
    """Rebuild this Path's occupancies from what it stored. **A-23**.

    Read back from the record rather than recomputed from the engine: the Payload Path behind
    it is versioned and may have been superseded since, and recomputing would reserve spectrum
    the operator never asked for. The stored edges *are* the allocation (`docs/design/02` §4.2).
    """
    from satnet_paths import services as path_services

    return path_services.occupancies_of(path)


def _require_scope(actor: Actor, path: SatnetPath, capability: str) -> None:
    allowed, why = satnet_scope.may_act_on(actor, beam_id=path.beam_id, hub_id=path.satnet.hub_id)
    if not allowed:
        policy.record_denial(actor, capability, path, detail=f"out of scope: {why}")
        raise TransitionRefused(why)


def _check_version(path: SatnetPath, expected_version: int | None) -> None:
    """§15.5, on a transition rather than on an edit.

    A button pressed on a page rendered ten minutes ago is the same problem as a stale form:
    the allocation may already have been approved, suspended or retired by somebody else, and
    the graph would happily accept the move from wherever it is now.
    """
    if expected_version is None or expected_version == path.record_version:
        return
    raise StaleRecordError(
        [FieldChange("status", None, path.status)], current_version=path.record_version
    )


# ---------------------------------------------------------------------------
# Editing in place — only where §15.4 allows it
# ---------------------------------------------------------------------------
#: The fields an operator may change on a record that is still editable. Everything else is
#: derived and system-owned (§26.16); the form does not bind those, and this list is the
#: service-side half of the same guarantee.
EDITABLE_FIELDS = (
    "code",
    "input_mode",
    "input_value",
    "rolloff",
    "guard_policy",
    "canonical_center_hz",
    "valid_from",
    "valid_until",
    "gateway",
    "decimator_assignment",
)


def edit(
    *,
    actor: Actor,
    path: SatnetPath,
    values: dict[str, Any],
    expected_version: int,
    reason: str = "",
) -> SatnetPath:
    """Change an editable allocation, recomputing everything derived from it.

    Refuses outside `DRAFT` and `PLANNED` (§15.4). The recomputation goes through the same
    ``preview`` the wizard uses, so an edit is checked exactly as a creation is — including
    against the reservations that exist *now*, which is the whole reason §9.5 asks for the
    check to be repeated on save.
    """
    from satnet_paths import services as path_services

    policy.require(actor, PLAN_SATNET_PATH, path, reason=reason)
    _require_scope(actor, path, PLAN_SATNET_PATH)

    if path.status not in EDITABLE_STATUSES:
        raise NotEditable(
            f"A Satnet Path in {path.status} cannot be edited in place (§15.4). Create a new "
            f"revision instead, which keeps the original as history."
        )
    _check_edit_version(path, values, expected_version)

    return path_services.rewrite(actor=actor, path=path, values=values, reason=reason)


def _check_edit_version(path: SatnetPath, values: dict[str, Any], expected_version: int) -> None:
    """§15.5 with the field-level difference the specification asks for.

    The comparison is against a **freshly read row**, not against the instance in hand. A
    ``ModelForm`` bound with ``instance=path`` writes the submitted values onto that instance
    while it validates, so comparing against it would compare the submission with itself and
    report no differences at all — a diff screen that is always empty, on the one screen whose
    entire job is to show what moved.
    """
    stored = SatnetPath.objects.get(pk=path.pk)
    if expected_version == stored.record_version:
        return

    changes = [
        FieldChange(field, values.get(field), getattr(stored, field))
        for field in EDITABLE_FIELDS
        if field in values and _differs(values.get(field), getattr(stored, field))
    ]
    audit_services.record(
        action=PATH_STALE,
        obj=path,
        after={"expected_version": expected_version, "current_version": path.record_version},
        message=f"Refused a stale submission for {path.code}",
    )
    raise StaleRecordError(changes, current_version=stored.record_version)


def _differs(submitted: Any, current: Any) -> bool:
    """Compare a submitted value with a stored one without tripping over their types.

    A form gives back model instances, `Decimal`s and datetimes; the stored side gives back
    the same things, except that a foreign key compares by identity. Stringifying both is
    crude and right for a *display* of what differs — the comparison exists to fill a diff
    table, not to decide anything.
    """
    return str(submitted) != str(current)


# ---------------------------------------------------------------------------
# Revising an allocation that may not be overwritten — §15.4, ADR-0014
# ---------------------------------------------------------------------------
@transaction.atomic
def revise(
    *,
    actor: Actor,
    path: SatnetPath,
    values: dict[str, Any],
    change_effective_at: datetime | None = None,
    reason: str = "",
    expected_version: int | None = None,
) -> SatnetPath:
    """Close an allocation and open its successor, in one transaction. §15.4.

    The order is the decision (`docs/design/02` §4.3, **A-14**):

    1. the old record's period closes at ``change_effective_at`` and it goes to `RETIRED`;
    2. its occupancy rows are released;
    3. the successor is written and reserves.

    Steps 1 and 2 *must* precede step 3. The exclusion constraint is `IMMEDIATE`, so a
    successor that overlaps its own predecessor — which is the normal case, since most
    revisions keep the same frequency — would be refused by the row it is replacing.
    """
    from satnet_paths import services as path_services

    policy.require(actor, REVISE_SATNET_PATH, path, reason=reason)
    _require_scope(actor, path, REVISE_SATNET_PATH)
    _check_version(path, expected_version)

    if path.superseded_by.exists():
        raise TransitionRefused(
            f"{path.code} revision {path.revision_number} has already been superseded. "
            f"Revise the current revision instead."
        )
    if path.status in {PathStatus.CANCELLED, PathStatus.RETIRED}:
        raise TransitionRefused(
            f"A {path.status} Satnet Path is history and is not revised — it is replaced by a "
            f"new allocation."
        )

    effective_at = change_effective_at or timezone.now()
    if effective_at <= path.valid_from:
        raise TransitionRefused(
            "A revision takes effect after the allocation it replaces began. Choose a moment "
            f"after {path.valid_from:%Y-%m-%d %H:%M} UTC."
        )

    predecessor_status = path.status
    path.valid_until = effective_at
    path.status = PathStatus.RETIRED
    path.record_version += 1
    path.save(update_fields=["valid_until", "status", "record_version", "updated_at"])
    spectrum_services.release(actor=actor, satnet_path_id=str(path.pk), reason=reason)

    try:
        successor = path_services.create_revision(
            actor=actor,
            predecessor=path,
            values={
                **_carried_forward(path),
                **values,
                "valid_from": effective_at,
                # A successor re-enters the graph where its predecessor's rules put it: an
                # approved allocation's replacement is not approved by inheritance (§15.4).
                "status": PathStatus.PENDING_APPROVAL
                if predecessor_status in {PathStatus.ON_AIR, PathStatus.PENDING_APPROVAL}
                else PathStatus.PLANNED,
            },
            reason=reason,
        )
    except spectrum_services.SpectrumConflictError as exc:
        raise TransitionRefused(str(exc)) from exc

    audit_services.record(
        action=PATH_REVISED,
        actor=actor,
        obj=successor,
        before={"revision": path.revision_number, "status": predecessor_status},
        after={"revision": successor.revision_number, "status": successor.status},
        change_reason=reason,
        message=(
            f"Revised {path.code}: revision {path.revision_number} closed at "
            f"{effective_at:%Y-%m-%d %H:%M} UTC, revision {successor.revision_number} opened"
        ),
    )
    return successor


def _carried_forward(path: SatnetPath) -> dict[str, Any]:
    """What a revision keeps unless the operator changes it.

    Only the *inputs*. Everything derived is recomputed by the service, because a revision
    that copied the stored edges forward would carry an old Payload Path's arithmetic into an
    allocation validated against the current one.
    """
    return {
        "code": path.code,
        "direction": path.direction,
        "input_mode": path.input_mode,
        "input_value": path.input_value,
        "rolloff": path.rolloff,
        "guard_policy": path.guard_policy,
        "canonical_center_hz": path.canonical_center_hz,
        "valid_until": path.valid_until,
        "gateway": path.gateway,
        "decimator_assignment": path.decimator_assignment,
    }


def revision_chain(path: SatnetPath) -> list[SatnetPath]:
    """Every revision of one allocation, oldest first. §15.4.

    One indexed query on ``revision_group``, which is why the column is constant across a
    chain rather than being reconstructed by walking ``supersedes``.
    """
    return list(
        SatnetPath.objects.filter(revision_group=path.revision_group).order_by("revision_number")
    )
