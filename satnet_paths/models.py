"""The Satnet Path: the single operator-facing allocation record. §9, §13.10.

Everything an operator does in this product ends here. The record stores what they *asked
for*, what the engine *computed*, and both sides of the payload as they were at the moment of
saving — and the reservations that hold the spectrum are written from it in the same
transaction (§15.6).

**Both sides are stored, not derived on read** (`docs/design/02` §4.2). The translated side is
a function of the canonical side and the Payload Path, and the Payload Path is master-data
versioned: recomputing on read would silently rewrite an allocation's history the moment its
path was superseded. The stored values are recomputed only by an explicit service call, which
produces an audit event.

**Every derived field is system-owned** (§26.16). ``SatnetPathForm`` never binds them; the
service writes them from the `calculations` result, and a test POSTs them directly to prove
they are ignored.
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import BigIntegerRangeField
from django.db import models
from django.db.models import F, Func, Value

from calculations.periods import TimePeriod
from inventory.constants import Direction, PolarizationType, SpectrumLeg
from inventory.models import TimestampedModel
from satnet_paths.constants import InputMode, PathStatus


def _range(start: str, end: str) -> models.GeneratedField:
    """A stored half-open ``int8range`` generated from two scalar columns.

    Generated rather than application-maintained for the reason `docs/design/04` §2 gives: the
    scalars are what a form binds and what a person reads, and a range column the application
    wrote could drift from them. This one cannot — it is not written by anything.
    """
    return models.GeneratedField(
        expression=Func(
            F(start), F(end), Value("[)"), function="int8range", output_field=BigIntegerRangeField()
        ),
        output_field=BigIntegerRangeField(),
        db_persist=True,
    )


class SatnetPath(TimestampedModel):
    """One direction-specific allocation under one Satnet. §13.10."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)

    satnet = models.ForeignKey("satnets.Satnet", on_delete=models.PROTECT, related_name="paths")
    #: Denormalised from the Satnet and pinned by a composite foreign key in the migration.
    #: Written by the service, never bound: a Path claiming a Beam its Satnet is not under
    #: would be judged against the wrong spectrum resources (ADR-0018).
    beam = models.ForeignKey("beams.Beam", on_delete=models.PROTECT, related_name="paths")
    direction = models.CharField(max_length=3, choices=Direction.choices)

    # --- Lifecycle (§15.2) -------------------------------------------------
    status = models.CharField(max_length=16, choices=PathStatus.choices, default=PathStatus.DRAFT)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True)

    # --- Revision (§15.4) --------------------------------------------------
    #: Constant across a revision chain, so the history view is one indexed query.
    revision_group = models.UUIDField(default=uuid.uuid4, editable=False)
    revision_number = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    # --- What the operator asked for (§9.2) --------------------------------
    input_mode = models.CharField(max_length=16, choices=InputMode.choices)
    input_value = models.BigIntegerField(
        help_text="Occupied bandwidth in Hz, or symbol rate in symbols per second."
    )
    rolloff = models.DecimalField(max_digits=4, decimal_places=3)
    guard_policy = models.ForeignKey(
        "inventory.GuardPolicy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="satnet_paths",
    )
    #: The **resolved** guard widths, not the policy's inputs. ADR-0016 resolves override →
    #: Satnet → Window → system, and storing the outcome means a policy edited later does not
    #: silently change what this allocation reserved.
    guard_left_hz = models.BigIntegerField(default=0)
    guard_right_hz = models.BigIntegerField(default=0)

    # --- Derived bandwidth (system-owned, §26.16) --------------------------
    symbol_rate_sps = models.BigIntegerField(null=True, blank=True)
    occupied_bw_hz = models.BigIntegerField()
    allocated_bw_hz = models.BigIntegerField()

    # --- Canonical side ----------------------------------------------------
    canonical_leg = models.CharField(max_length=16, choices=SpectrumLeg.choices)
    canonical_window = models.ForeignKey(
        "inventory.FrequencyWindow", on_delete=models.PROTECT, related_name="paths_as_canonical"
    )
    canonical_assignment = models.ForeignKey(
        "beams.BeamSpectrumAssignment",
        on_delete=models.PROTECT,
        related_name="paths_as_canonical",
    )
    canonical_center_hz = models.BigIntegerField()
    canonical_occupied_start_hz = models.BigIntegerField()
    canonical_occupied_end_hz = models.BigIntegerField()
    canonical_allocated_start_hz = models.BigIntegerField()
    canonical_allocated_end_hz = models.BigIntegerField()
    canonical_polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    # --- Translated side ---------------------------------------------------
    translated_leg = models.CharField(max_length=16, choices=SpectrumLeg.choices)
    translated_window = models.ForeignKey(
        "inventory.FrequencyWindow", on_delete=models.PROTECT, related_name="paths_as_translated"
    )
    translated_assignment = models.ForeignKey(
        "beams.BeamSpectrumAssignment",
        on_delete=models.PROTECT,
        related_name="paths_as_translated",
    )
    translated_center_hz = models.BigIntegerField()
    translated_occupied_start_hz = models.BigIntegerField()
    translated_occupied_end_hz = models.BigIntegerField()
    translated_allocated_start_hz = models.BigIntegerField()
    translated_allocated_end_hz = models.BigIntegerField()
    translated_polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    canonical_occupied_rf = _range("canonical_occupied_start_hz", "canonical_occupied_end_hz")
    canonical_allocated_rf = _range("canonical_allocated_start_hz", "canonical_allocated_end_hz")
    translated_occupied_rf = _range("translated_occupied_start_hz", "translated_occupied_end_hz")
    translated_allocated_rf = _range("translated_allocated_start_hz", "translated_allocated_end_hz")

    # --- Equipment and IF (§9.4, "where applicable") -----------------------
    equipment_profile = models.ForeignKey(
        "inventory.EquipmentProfile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="satnet_paths",
    )
    lo_hz = models.BigIntegerField(null=True, blank=True)
    if_start_hz = models.BigIntegerField(null=True, blank=True)
    if_center_hz = models.BigIntegerField(null=True, blank=True)
    if_end_hz = models.BigIntegerField(null=True, blank=True)

    # --- Hardware (OQ-09, OQ-10) -------------------------------------------
    #: **A-26.** A controlled reference, never a contention boundary. The OQ-09 answer is
    #: explicit that *"double-booking shall not be determined from GW ID"* — many Hubs and many
    #: Paths may name the same Gateway, and two allocations that share one do not conflict on
    #: that account. It appears in no occupancy row and in no exclusion key, and a test asserts
    #: that it never will.
    gateway = models.ForeignKey(
        "inventory.Gateway",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="satnet_paths",
        verbose_name="GW ID",
        help_text="The Gateway this Path runs through. A shared reference, not a reservation.",
    )
    #: **A-27**, and the opposite answer to the field above it. A Decimator *is* allocatable, so
    #: the Path points at the time-bounded `DecimatorAssignment` rather than at the box or at a
    #: free-text name. Many Paths may share one assignment (fan-out, broadcast, multicast); what
    #: is forbidden is two overlapping assignments on one Decimator, which is enforced on the
    #: assignment table where it belongs.
    decimator_assignment = models.ForeignKey(
        "inventory.DecimatorAssignment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="satnet_paths",
        help_text="The Decimator configuration this Path consumes. Several Paths may share one.",
    )

    class Meta:
        db_table = "satnet_path"
        ordering = ["satnet__code", "code", "-revision_number"]
        default_permissions = ("view",)
        #: One per transition, not one per screen. `docs/design/03` §2.2 keeps them separate
        #: because the roles genuinely differ — an Operator plans and submits, an Approver
        #: decides and retires — and a single "change lifecycle" capability would hand the
        #: Operator the approval the Approver role exists to be separate from.
        permissions = [
            ("manage_satnet_paths", "Can create and change Satnet Paths"),
            ("plan_satnetpath", "Can move a Satnet Path from draft to planned"),
            ("submit_satnetpath", "Can submit a Satnet Path for approval"),
            ("approve_satnetpath", "Can approve a Satnet Path onto air"),
            ("reject_satnetpath", "Can reject a Satnet Path back to planned"),
            ("suspend_satnetpath", "Can suspend and resume an on-air Satnet Path"),
            ("retire_satnetpath", "Can retire a Satnet Path"),
            ("cancel_satnetpath", "Can cancel a draft or planned Satnet Path"),
            ("revise_satnetpath", "Can create a new revision of a Satnet Path"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["satnet", "code", "revision_number"], name="uq_path_satnet_code_revision"
            ),
            models.UniqueConstraint(
                fields=["revision_group", "revision_number"], name="uq_path_revision"
            ),
            # §20's list, on both sides.
            models.CheckConstraint(
                condition=models.Q(symbol_rate_sps__isnull=True) | models.Q(symbol_rate_sps__gt=0),
                name="ck_path_symbol_rate",
            ),
            models.CheckConstraint(
                condition=models.Q(rolloff__gte=0) & models.Q(rolloff__lte=1),
                name="ck_path_rolloff",
            ),
            models.CheckConstraint(
                condition=models.Q(guard_left_hz__gte=0) & models.Q(guard_right_hz__gte=0),
                name="ck_path_guards",
            ),
            models.CheckConstraint(
                condition=models.Q(occupied_bw_hz__gt=0)
                & models.Q(allocated_bw_hz__gte=models.F("occupied_bw_hz")),
                name="ck_path_bw_positive",
            ),
            # §20: the centre sits inside the occupied range, on both sides. A centre outside
            # its own transmission is the sort of thing that only happens through a service
            # bug, and it is exactly the sort of bug that produces a plausible-looking row.
            models.CheckConstraint(
                condition=models.Q(canonical_center_hz__gte=models.F("canonical_occupied_start_hz"))
                & models.Q(canonical_center_hz__lt=models.F("canonical_occupied_end_hz"))
                & models.Q(translated_center_hz__gte=models.F("translated_occupied_start_hz"))
                & models.Q(translated_center_hz__lt=models.F("translated_occupied_end_hz")),
                name="ck_path_center_in_occupied",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    canonical_occupied_start_hz__gte=models.F("canonical_allocated_start_hz")
                )
                & models.Q(canonical_occupied_end_hz__lte=models.F("canonical_allocated_end_hz"))
                & models.Q(
                    translated_occupied_start_hz__gte=models.F("translated_allocated_start_hz")
                )
                & models.Q(translated_occupied_end_hz__lte=models.F("translated_allocated_end_hz")),
                name="ck_path_occ_in_alloc",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gt=models.F("valid_from")),
                name="ck_path_validity",
            ),
            # **A-03**: a FWD path runs hub uplink to remote downlink; RTN the other way.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        direction=Direction.FWD,
                        canonical_leg=SpectrumLeg.HUB_UPLINK,
                        translated_leg=SpectrumLeg.REMOTE_DOWNLINK,
                    )
                    | models.Q(
                        direction=Direction.RTN,
                        canonical_leg=SpectrumLeg.REMOTE_UPLINK,
                        translated_leg=SpectrumLeg.HUB_DOWNLINK,
                    )
                ),
                name="ck_path_legs",
            ),
            models.CheckConstraint(
                condition=models.Q(input_value__gt=0), name="ck_path_input_value"
            ),
            models.CheckConstraint(
                condition=models.Q(if_start_hz__isnull=True)
                | models.Q(if_start_hz__gte=0, if_start_hz__lt=models.F("if_end_hz")),
                name="ck_path_if",
            ),
            # §15.4: revision 1 supersedes nothing, and every later revision supersedes
            # something. Without this a chain can lose its head and the history view shows an
            # allocation that appears to have replaced nothing.
            models.CheckConstraint(
                condition=models.Q(revision_number=1, supersedes__isnull=True)
                | models.Q(revision_number__gt=1, supersedes__isnull=False),
                name="ck_path_revision",
            ),
        ]
        indexes = [
            models.Index(fields=["satnet", "status"], name="path_satnet_status_idx"),
            models.Index(fields=["revision_group", "revision_number"], name="path_revision_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} r{self.revision_number} ({self.direction})"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("satnet_paths:detail", kwargs={"pk": self.pk})

    @property
    def validity(self) -> TimePeriod:
        return TimePeriod(self.valid_from, self.valid_until)

    @property
    def is_operational(self) -> bool:
        """Does this record hold spectrum, or is it still a draft? **OQ-32**.

        The distinction the containment answer turns on: a draft may sit outside its parents'
        periods with warnings, and an operational record may not.
        """
        return self.status != PathStatus.DRAFT
