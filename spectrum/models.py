"""The reservation table and the constraint that makes the platform's promise true.

Specification sections 8.1 to 8.4, §13.11, §20. ADR-0007, ADR-0018.

Everything else in this product describes spectrum. This table **guarantees** it: two
allocations cannot occupy the same Hz on the same resource at the same time, and the
guarantee is a database constraint rather than a service check, because §8.3 says so and
because a service check is only as good as the last code path somebody remembered to route
through it.
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    BigIntegerRangeField,
    DateTimeRangeField,
    RangeOperators,
)
from django.db import models
from django.db.models import F, Func, Value

from inventory.constants import Direction, PolarizationType, SpectrumLeg
from inventory.models import TimestampedModel
from spectrum.constants import ALWAYS_RESERVING, NEVER_RESERVING, ReservationKind, ReservationStatus


class SpectrumReservation(TimestampedModel):
    """One occupancy record: this resource, this RF, for this period.

    **One allocation writes several of these** — N ≥ 2, not two (**A-23**). OQ-25 is explicit
    that *"an allocation may reserve more than one spectrum resource"*, so the canonical and
    translated sides of a Satnet Path each produce one row per resource their leg occupies.
    ADR-0006's two-sided model is still true of the *engineering*; it is no longer the row
    count, and anything that assumes a pair will be wrong the first time a leg shares two
    chains.

    **There is no write route to this table for any role** (§13.11). Reservations are written
    by ``spectrum.services`` inside the transaction that creates the allocation they belong
    to, and by nothing else. A screen that could edit a reservation directly would be a screen
    that could put the reservation and its Satnet Path into disagreement, and the reservation
    is what the constraint sees.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # --- What is occupied -------------------------------------------------
    #: The reuse key (ADR-0018). Two rows conflict when this matches and their RF and time
    #: both overlap — nothing else about them is compared.
    spectrum_resource = models.ForeignKey(
        "inventory.SpectrumResource", on_delete=models.PROTECT, related_name="reservations"
    )
    #: The entitlement this occupancy sits inside (ADR-0019). Stored rather than resolved on
    #: read so the containment CHECK below is per-row, and so a superseded assignment does
    #: not silently re-point a live reservation at different bounds.
    beam_spectrum_assignment = models.ForeignKey(
        "beams.BeamSpectrumAssignment", on_delete=models.PROTECT, related_name="reservations"
    )
    #: Denormalised from the assignment and pinned by a composite foreign key in the
    #: migration. Written by the service, never by a form.
    assignment_start_hz = models.BigIntegerField()
    assignment_end_hz = models.BigIntegerField()

    leg = models.CharField(max_length=16, choices=SpectrumLeg.choices)
    polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    occupied_start_hz = models.BigIntegerField()
    occupied_end_hz = models.BigIntegerField()
    #: **Guards included.** §8.1 makes the reserved interval the allocated one, and §8.2 warns
    #: against comparing centre frequencies. This is what the exclusion constraint compares.
    allocated_start_hz = models.BigIntegerField()
    allocated_end_hz = models.BigIntegerField()

    occupied_rf = models.GeneratedField(
        expression=Func(
            F("occupied_start_hz"),
            F("occupied_end_hz"),
            Value("[)"),
            function="int8range",
            output_field=BigIntegerRangeField(),
        ),
        output_field=BigIntegerRangeField(),
        db_persist=True,
    )
    allocated_rf = models.GeneratedField(
        expression=Func(
            F("allocated_start_hz"),
            F("allocated_end_hz"),
            Value("[)"),
            function="int8range",
            output_field=BigIntegerRangeField(),
        ),
        output_field=BigIntegerRangeField(),
        db_persist=True,
    )

    # --- When -------------------------------------------------------------
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    active_period = models.GeneratedField(
        expression=Func(
            F("valid_from"),
            F("valid_until"),
            Value("[)"),
            function="tstzrange",
            output_field=DateTimeRangeField(),
        ),
        output_field=DateTimeRangeField(),
        db_persist=True,
    )

    # --- Who it belongs to -------------------------------------------------
    kind = models.CharField(
        max_length=16, choices=ReservationKind.choices, default=ReservationKind.SATNET_PATH
    )
    #: No foreign key yet — ``satnet_path`` does not exist until S11, which is deliberate:
    #: §04 §3 builds the enforcement layer *before* the feature that depends on it, so the
    #: concurrency test exists before the first reservation is ever written. S11 adds
    #: ``fk_reservation_satnet_path`` to a table that is still empty.
    satnet_path_id = models.UUIDField(null=True, blank=True, db_index=True)
    direction = models.CharField(max_length=3, choices=Direction.choices, blank=True)
    status = models.CharField(max_length=16, choices=ReservationStatus.choices, blank=True)

    #: **A-12.** A partial index predicate must be `IMMUTABLE`, and whether a `SUSPENDED`
    #: allocation holds spectrum is a runtime setting (§15.3, **OQ-08**) — so the answer is
    #: computed by the service and stored, and a CHECK pins every status whose policy is not
    #: configurable.
    reserves_spectrum = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    class Meta:
        db_table = "spectrum_reservation"
        ordering = ["spectrum_resource__code", "allocated_start_hz"]
        # No add/change/delete for anyone: §13.11 says there is no write route, and a
        # permission that exists is a permission somebody eventually grants.
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(allocated_start_hz__lt=models.F("allocated_end_hz")),
                name="ck_res_alloc_start_lt_end",
            ),
            models.CheckConstraint(
                condition=models.Q(occupied_start_hz__lt=models.F("occupied_end_hz")),
                name="ck_res_occ_start_lt_end",
            ),
            # §20: the occupied range sits inside the allocated one. Expressed on the scalar
            # columns rather than with `@>` so Django can build it — the generated range
            # columns are equivalent by construction.
            models.CheckConstraint(
                condition=models.Q(occupied_start_hz__gte=models.F("allocated_start_hz"))
                & models.Q(occupied_end_hz__lte=models.F("allocated_end_hz")),
                name="ck_res_occ_in_alloc",
            ),
            # ADR-0019. **Allocated**, not occupied: guards are part of what is reserved
            # (§8.1), and spectrum outside this Beam's entitlement belongs to somebody else.
            # A transmission that needs a guard at the edge of its assignment needs a wider
            # assignment, not permission to reserve beyond it.
            models.CheckConstraint(
                condition=models.Q(allocated_start_hz__gte=models.F("assignment_start_hz"))
                & models.Q(allocated_end_hz__lte=models.F("assignment_end_hz")),
                name="ck_res_within_assignment",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gt=models.F("valid_from")),
                name="ck_res_period",
            ),
            # **A-13.** A Satnet Path reservation names its path and its status; a fixed
            # reserve has neither, and must not pretend to.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind=ReservationKind.SATNET_PATH,
                        satnet_path_id__isnull=False,
                    )
                    & ~models.Q(status="")
                    | models.Q(
                        kind=ReservationKind.FIXED_RESERVE,
                        satnet_path_id__isnull=True,
                        status="",
                        direction="",
                    )
                ),
                name="ck_res_kind_path",
            ),
            # **A-12.** Every status whose policy is fixed is pinned here. SUSPENDED is
            # absent on purpose: pinning it would answer OQ-08 by implication.
            models.CheckConstraint(
                condition=(
                    models.Q(status__in=ALWAYS_RESERVING, reserves_spectrum=True)
                    | models.Q(status__in=NEVER_RESERVING, reserves_spectrum=False)
                    | models.Q(status=ReservationStatus.SUSPENDED)
                    | models.Q(status="")
                ),
                name="ck_res_reserves_status",
            ),
            # §20: a FWD allocation runs hub uplink to remote downlink; RTN runs the other
            # way (**A-03**). A fixed reserve has no direction and is exempt.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        direction=Direction.FWD,
                        leg__in=[SpectrumLeg.HUB_UPLINK, SpectrumLeg.REMOTE_DOWNLINK],
                    )
                    | models.Q(
                        direction=Direction.RTN,
                        leg__in=[SpectrumLeg.REMOTE_UPLINK, SpectrumLeg.HUB_DOWNLINK],
                    )
                    | models.Q(direction="")
                ),
                name="ck_res_direction_leg",
            ),
            # ---------------------------------------------------------------
            # The promise
            # ---------------------------------------------------------------
            # Three columns, where the superseded Beam-keyed design had six (ADR-0018).
            # Same resource, overlapping allocated RF *including guards*, overlapping active
            # time, and a status that holds spectrum.
            #
            # Not DEFERRABLE: immediate checking attributes the violation to the offending
            # statement, which is what makes the §9.5 error message possible at all
            # (**A-14**).
            ExclusionConstraint(
                name="excl_reservation_overlap",
                expressions=[
                    ("spectrum_resource", RangeOperators.EQUAL),
                    ("allocated_rf", RangeOperators.OVERLAPS),
                    ("active_period", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(reserves_spectrum=True),
            ),
        ]
        indexes = [
            models.Index(
                fields=["beam_spectrum_assignment", "allocated_start_hz"],
                name="reservation_capacity_idx",
                condition=models.Q(reserves_spectrum=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.spectrum_resource_id} {self.allocated_start_hz}-{self.allocated_end_hz}"

    @property
    def allocated_width_hz(self) -> int:
        return self.allocated_end_hz - self.allocated_start_hz

    @property
    def occupied_width_hz(self) -> int:
        return self.occupied_end_hz - self.occupied_start_hz

    @property
    def guard_left_hz(self) -> int:
        return self.occupied_start_hz - self.allocated_start_hz

    @property
    def guard_right_hz(self) -> int:
        return self.allocated_end_hz - self.occupied_end_hz
