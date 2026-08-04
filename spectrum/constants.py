"""Reservation kinds, lifecycle statuses, and which of them hold spectrum.

Specification sections 13.11, 15.2, 15.3.
"""

from __future__ import annotations

from django.db import models


class ReservationKind(models.TextChoices):
    """Why a piece of spectrum is held. **A-13**.

    §16 subtracts "fixed reserve areas" from free spectrum, and a PostgreSQL exclusion
    constraint cannot span two tables — so a fixed reserve has to live in the same table as
    an allocation or the two could overlap each other freely.
    """

    SATNET_PATH = "SATNET_PATH", "Held by a Satnet Path"
    FIXED_RESERVE = "FIXED_RESERVE", "Fixed reserved area"


class ReservationStatus(models.TextChoices):
    """The §15.2 lifecycle, denormalised onto the reservation.

    Stored here as well as on the Satnet Path because the exclusion constraint's partial
    index predicate has to be evaluable from this row alone.
    """

    DRAFT = "DRAFT", "Draft"
    PLANNED = "PLANNED", "Planned"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending approval"
    ON_AIR = "ON_AIR", "On air"
    SUSPENDED = "SUSPENDED", "Suspended"
    CANCELLED = "CANCELLED", "Cancelled"
    RETIRED = "RETIRED", "Retired"
    IMPORT_REVIEW = "IMPORT_REVIEW", "Awaiting import review"


#: Statuses that always hold spectrum, and always will. Pinned by a CHECK (**A-12**).
ALWAYS_RESERVING = (
    ReservationStatus.PLANNED,
    ReservationStatus.PENDING_APPROVAL,
    ReservationStatus.ON_AIR,
)

#: Statuses that never hold spectrum. Also pinned by the same CHECK.
NEVER_RESERVING = (
    ReservationStatus.DRAFT,
    ReservationStatus.CANCELLED,
    ReservationStatus.RETIRED,
    ReservationStatus.IMPORT_REVIEW,
)

#: `SUSPENDED` is in neither list, and that is the whole of **OQ-08**: whether a suspended
#: allocation keeps its spectrum is a runtime setting (§15.3), so the CHECK cannot pin it
#: without deciding an open question by implication.

VIEW_SPECTRUM = "spectrum.view_spectrumreservation"

RESERVATION_WRITTEN = "SPECTRUM_RESERVED"
RESERVATION_RELEASED = "SPECTRUM_RELEASED"
