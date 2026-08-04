"""Writing reservations. The only route to the table. §13.11, §15.6.

There is **no HTTP write path** to `spectrum_reservation` for any role, and no form. These
functions are called by the allocation services that own the thing being reserved — the Satnet
Path wizard in S11, the importer in S15 — inside the same transaction that writes it.

That is not a permissions decision that could be relaxed later. A reservation is the row the
exclusion constraint compares; a screen that could edit one directly would be a screen that
could put a reservation and the allocation it belongs to into disagreement, and the constraint
would go on enforcing whatever the reservation said.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from django.db import IntegrityError, OperationalError, transaction

from accounts.types import Actor
from audit import services as audit_services
from beams.models import BeamSpectrumAssignment
from calculations.ranges import FrequencyRange
from spectrum.constants import (
    ALWAYS_RESERVING,
    NEVER_RESERVING,
    RESERVATION_RELEASED,
    RESERVATION_WRITTEN,
    ReservationKind,
    ReservationStatus,
)
from spectrum.models import SpectrumReservation


class SpectrumConflictError(Exception):
    """This allocation collides with spectrum somebody else holds.

    **Two different database errors mean this**, which the concurrency test found and which is
    not obvious from reading the constraint:

    * ``IntegrityError`` naming ``excl_reservation_overlap`` — the ordinary case, where the
      competing row was already committed;
    * ``OperationalError: deadlock detected`` — the *concurrent* case. Two transactions
      inserting mutually overlapping rows each take a lock the other needs while checking the
      exclusion constraint, and PostgreSQL resolves it by killing one. Which one is arbitrary.

    Both are one thing to an operator: the spectrum is taken. Leaving the deadlock
    untranslated would give whoever lost the race a 500 instead of §9.5's message, on a
    perfectly ordinary Tuesday-morning collision between two people planning at once.

    ``was_deadlock`` is carried because the two differ in one respect that matters upstream:
    a deadlock means the competing allocation may itself have been rolled back, so a retry
    can legitimately succeed where the plain overlap case never would.
    """

    def __init__(self, message: str, *, was_deadlock: bool = False) -> None:
        self.was_deadlock = was_deadlock
        super().__init__(message)


class OutsideEntitlementError(Exception):
    """The allocation does not fit inside an active assignment, in RF or in time.

    Carries the assignment it was checked against. A refusal that only says "outside" leaves
    an operator guessing which of a Beam's sub-ranges they were near.
    """

    def __init__(self, message: str, assignment: BeamSpectrumAssignment | None = None) -> None:
        self.assignment = assignment
        super().__init__(message)


@dataclasses.dataclass(frozen=True)
class Occupancy:
    """One leg's spectrum, and the resources it competes on.

    A leg rather than a side: **A-23** means one leg may occupy several resources, and this is
    the unit the caller can actually state. Turning it into rows is this module's job.
    """

    assignment: BeamSpectrumAssignment
    leg: str
    polarization: str
    occupied: FrequencyRange
    allocated: FrequencyRange
    resource_ids: tuple[str, ...]


def reserves_spectrum(status: str, *, suspended_retains: bool) -> bool:
    """Does an allocation in this status hold its spectrum? **A-12**, **OQ-08**.

    One function, so the answer cannot differ between the service that writes the column and
    the screen that explains it. `SUSPENDED` is the only status that consults the setting;
    every other answer is pinned by `ck_res_reserves_status` and would be rejected by the
    database if this disagreed with it.
    """
    if status in ALWAYS_RESERVING:
        return True
    if status in NEVER_RESERVING:
        return False
    if status == ReservationStatus.SUSPENDED:
        return suspended_retains
    return False


@transaction.atomic
def reserve(
    *,
    actor: Actor,
    occupancies: list[Occupancy],
    satnet_path_id: str | None,
    direction: str,
    status: str,
    valid_from: datetime,
    valid_until: datetime | None = None,
    suspended_retains: bool = True,
    reason: str = "",
) -> list[SpectrumReservation]:
    """Write every occupancy row for one allocation, or none of them.

    **All in one transaction**, because a half-written allocation is worse than a refused one:
    the rows that did commit would hold spectrum for a Satnet Path that does not exist, and
    nothing would ever release them.

    The exclusion constraint is `IMMEDIATE` (**A-14**), so a conflict raises on the offending
    `INSERT` rather than at `COMMIT` — which is what lets the caller name the row that
    collided instead of reporting that something, somewhere, overlapped.
    """
    holds = reserves_spectrum(status, suspended_retains=suspended_retains)
    rows: list[SpectrumReservation] = []

    for occupancy in occupancies:
        _check_entitlement(occupancy, valid_from, valid_until)
        for resource_id in occupancy.resource_ids:
            rows.append(
                _write(
                    spectrum_resource_id=resource_id,
                    beam_spectrum_assignment=occupancy.assignment,
                    assignment_start_hz=occupancy.assignment.rf_start_hz,
                    assignment_end_hz=occupancy.assignment.rf_end_hz,
                    leg=occupancy.leg,
                    polarization=occupancy.polarization,
                    occupied_start_hz=occupancy.occupied.start_hz,
                    occupied_end_hz=occupancy.occupied.end_hz,
                    allocated_start_hz=occupancy.allocated.start_hz,
                    allocated_end_hz=occupancy.allocated.end_hz,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    kind=ReservationKind.SATNET_PATH,
                    satnet_path_id=satnet_path_id,
                    direction=direction,
                    status=status,
                    reserves_spectrum=holds,
                )
            )

    audit_services.record(
        action=RESERVATION_WRITTEN,
        actor=actor,
        after={
            "satnet_path_id": str(satnet_path_id) if satnet_path_id else None,
            "rows": len(rows),
            "resources": sorted({str(row.spectrum_resource_id) for row in rows}),
            "reserves_spectrum": holds,
        },
        change_reason=reason,
        message=f"Reserved spectrum on {len(rows)} resource occupancies",
    )
    return rows


@transaction.atomic
def release(*, actor: Actor, satnet_path_id: str, reason: str = "") -> int:
    """Drop every reservation belonging to one allocation.

    A hard delete, unlike everything else in the product, and the exception is deliberate: a
    reservation is not a record of what happened — the audit trail is (§18) — it is a claim on
    spectrum that either holds or does not. A soft-deleted reservation would need
    `reserves_spectrum = false`, at which point the row is doing nothing the audit event does
    not do better while still being visible to every query that forgets the filter.
    """
    queryset = SpectrumReservation.objects.filter(satnet_path_id=satnet_path_id)
    released = [
        {
            "resource": str(row.spectrum_resource_id),
            "allocated": [row.allocated_start_hz, row.allocated_end_hz],
        }
        for row in queryset
    ]
    count, _ = queryset.delete()

    audit_services.record(
        action=RESERVATION_RELEASED,
        actor=actor,
        before={"satnet_path_id": str(satnet_path_id), "rows": released},
        change_reason=reason,
        message=f"Released {count} spectrum reservations",
    )
    return count


def _write(**values: object) -> SpectrumReservation:
    """One `INSERT`, with both conflict shapes translated into one exception.

    The exclusion constraint refuses an overlap in two different ways depending on whether the
    competing row is already committed or is being written right now, and a caller that only
    catches ``IntegrityError`` handles half the cases — the half that does not happen when two
    operators are actually working at the same time.
    """
    try:
        return SpectrumReservation.objects.create(**values)
    except IntegrityError as exc:
        if "excl_reservation_overlap" in str(exc):
            raise SpectrumConflictError(
                "This spectrum is already reserved on one of the resources it occupies."
            ) from exc
        raise
    except OperationalError as exc:
        if "deadlock detected" in str(exc):
            raise SpectrumConflictError(
                "Another allocation for overlapping spectrum was committed at the same moment. "
                "Nothing was saved; try again.",
                was_deadlock=True,
            ) from exc
        raise


def _check_entitlement(
    occupancy: Occupancy, valid_from: datetime, valid_until: datetime | None
) -> None:
    """The half of containment the database cannot hold. ADR-0019.

    RF containment is `ck_res_within_assignment` and needs no help. **Time containment is
    here**, because it cannot be a composite foreign key: an open-ended assignment has
    ``effective_until IS NULL``, and a MATCH SIMPLE key with a NULL in any column is satisfied
    trivially — so the constraint would be vacuous in the common case, which is worse than
    absent because it would be there to read.

    Both are checked together and against the same assignment. Checking the RF and forgetting
    the period gives an allocation that is valid today and silently outside its entitlement
    next month.
    """
    assignment = occupancy.assignment

    if not assignment.is_active:
        raise OutsideEntitlementError(
            f"Assignment {assignment.pk} is not active, so this Beam may not use it.",
            assignment,
        )
    if valid_from < assignment.effective_from:
        raise OutsideEntitlementError(
            f"The allocation starts {valid_from:%Y-%m-%d}, before its spectrum assignment "
            f"begins on {assignment.effective_from:%Y-%m-%d}.",
            assignment,
        )
    if assignment.effective_until is not None and (
        valid_until is None or valid_until > assignment.effective_until
    ):
        ends = "open-ended" if valid_until is None else f"{valid_until:%Y-%m-%d}"
        raise OutsideEntitlementError(
            f"The allocation runs to {ends}, past the end of its spectrum assignment on "
            f"{assignment.effective_until:%Y-%m-%d}.",
            assignment,
        )
