"""Dependent inventory master data.

Specification sections 3.2, 13.6 and 13.7. These entities require at least one independent
object to exist first, and they are where the RF model starts to bite:

* a **Frequency Window** is the only thing that grants permission to allocate spectrum
  (§13.2: band limits are informative, windows are authoritative);
* a **Payload Path** must produce a *deterministic* mapping from one RF side to the other
  (§13.7), which is why its translation method and constant are modelled explicitly rather
  than being derived from the windows;
* both are **master-data versioned** (**A-16**): a window in operational use is changed by
  creating a new version, never by retroactive overwrite (§13.6).
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models

from inventory.constants import (
    Direction,
    GuardMode,
    PolarizationType,
    SpectrumLeg,
    TranslationMethod,
)
from inventory.models.base import InventoryRecord, MasterDataVersioned


class GuardPolicy(InventoryRecord):
    """A named rule for the separation applied either side of a transmission.

    Not in the specification's entity list, but required by it: §13.6 gives a Frequency
    Window a "default guard policy" and §13.9 gives a Satnet one, so the thing they both
    default to needs somewhere to live. §9.2 additionally lets an operator "select or
    accept" a policy, which means it must be selectable — a bare pair of numbers would not
    be.

    **No policy is seeded.** Guard values by Band, Window and platform are **OQ-07**.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    mode = models.CharField(max_length=28, choices=GuardMode.choices)

    fixed_left_hz = models.BigIntegerField(null=True, blank=True)
    fixed_right_hz = models.BigIntegerField(null=True, blank=True)
    # Percentage of the occupied bandwidth. Exact decimal, never float: a guard derived
    # through binary floating point would put an approximate edge on an interval the
    # database compares exactly (ADR-0003).
    percent_left = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    percent_right = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)

    description = models.TextField(blank=True)

    class Meta:
        db_table = "guard_policy"
        ordering = ["code"]
        default_permissions = ("view",)
        verbose_name_plural = "Guard policies"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fixed_left_hz__isnull=True) | models.Q(fixed_left_hz__gte=0),
                name="ck_guard_fixed_left_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(fixed_right_hz__isnull=True) | models.Q(fixed_right_hz__gte=0),
                name="ck_guard_fixed_right_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(percent_left__isnull=True) | models.Q(percent_left__gte=0),
                name="ck_guard_percent_left_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(percent_right__isnull=True) | models.Q(percent_right__gte=0),
                name="ck_guard_percent_right_non_negative",
            ),
            # A policy that carries none of the values its own mode needs would resolve to
            # a zero guard silently, which is the one failure mode a guard must not have.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        mode=GuardMode.FIXED,
                        fixed_left_hz__isnull=False,
                        fixed_right_hz__isnull=False,
                    )
                    | models.Q(
                        mode=GuardMode.PERCENT_OF_OCCUPIED,
                        percent_left__isnull=False,
                        percent_right__isnull=False,
                    )
                    | models.Q(
                        mode=GuardMode.MAX_OF_FIXED_AND_PERCENT,
                        fixed_left_hz__isnull=False,
                        fixed_right_hz__isnull=False,
                        percent_left__isnull=False,
                        percent_right__isnull=False,
                    )
                ),
                name="ck_guard_mode_has_required_values",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:guard-policy-detail", kwargs={"pk": self.pk})


class FrequencyWindow(InventoryRecord, MasterDataVersioned):
    """Allocatable spectrum on one leg of one satellite. Specification section 13.6.

    The authoritative grant of permission to allocate: §13.2 makes Band limits merely
    informative, and it is the Window that an allocation must fit inside.

    One ``polarization`` per window, not a set. §25 states that different polarizations are
    separable *"only when they are separate configured Frequency Windows"*, so two
    polarizations on one satellite leg are two windows — which is also what lets the
    overlap constraint key on the window and stay correct (**A-04**).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)

    satellite = models.ForeignKey(
        "inventory.Satellite", on_delete=models.PROTECT, related_name="frequency_windows"
    )
    band = models.ForeignKey(
        "inventory.Band", on_delete=models.PROTECT, related_name="frequency_windows"
    )
    side = models.CharField(
        max_length=16,
        choices=SpectrumLeg.choices,
        help_text="Which leg of the payload chain this window covers.",
    )
    polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    rf_start_hz = models.BigIntegerField()
    rf_end_hz = models.BigIntegerField(help_text="Exclusive upper edge: the range is [start, end).")
    min_edge_guard_hz = models.BigIntegerField(
        default=0, help_text="Minimum separation from either edge of the window."
    )
    default_guard_policy = models.ForeignKey(
        GuardPolicy,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="frequency_windows",
    )

    source_reference = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "frequency_window"
        ordering = ["satellite__code", "side", "rf_start_hz"]
        default_permissions = ("view",)
        constraints = [
            models.UniqueConstraint(
                fields=["satellite", "code", "version_number"],
                name="uq_window_satellite_code_version",
            ),
            models.UniqueConstraint(
                fields=["version_group", "version_number"], name="uq_window_group_version"
            ),
            # Targets for the composite foreign keys that let PayloadPath and, later,
            # SpectrumReservation carry a denormalised side and polarization that the
            # database itself keeps honest (docs/design/04 section 3.2).
            models.UniqueConstraint(fields=["id", "side"], name="uq_window_id_side"),
            models.UniqueConstraint(
                fields=["id", "side", "polarization"], name="uq_window_id_side_polarization"
            ),
            models.CheckConstraint(
                condition=models.Q(rf_start_hz__lt=models.F("rf_end_hz")),
                name="ck_window_start_lt_end",
            ),
            models.CheckConstraint(
                condition=models.Q(min_edge_guard_hz__gte=0),
                name="ck_window_edge_guard_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_window_effective_period",
            ),
            # Two versions of the same logical window must never be active at once: an
            # allocation would then have two different definitions of what it must fit
            # inside, and nothing would say which one wins (A-16).
            ExclusionConstraint(
                name="excl_window_version_overlap",
                expressions=[
                    ("version_group", RangeOperators.EQUAL),
                    ("effective_period", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(is_active=True),
            ),
        ]
        indexes = [
            models.Index(
                fields=["satellite", "side", "polarization"],
                name="window_lookup_idx",
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} v{self.version_number} ({self.side}/{self.polarization})"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:frequency-window-detail", kwargs={"pk": self.pk})

    @property
    def width_hz(self) -> int:
        return self.rf_end_hz - self.rf_start_hz

    @property
    def is_uplink(self) -> bool:
        return self.side in {SpectrumLeg.HUB_UPLINK, SpectrumLeg.REMOTE_UPLINK}

    def contains(self, start_hz: int, end_hz: int) -> bool:
        """Does the half-open interval ``[start_hz, end_hz)`` fit inside this window?

        Edge guards are **not** applied here: whether the minimum edge guard is part of
        the allocated range or a separate validation is **OQ-34**, and answering it by
        implication would settle an open question by accident.
        """
        return self.rf_start_hz <= start_hz and end_hz <= self.rf_end_hz


class PayloadPath(InventoryRecord, MasterDataVersioned):
    """Satellite translation between an uplink and a downlink window. Section 13.7.

    §13.7 requires a **deterministic** mapping from one RF side to the other, which is why
    the method and constant are stored rather than inferred: two windows can sit at any
    offset from each other, and guessing the relationship from their edges would produce a
    plausible number that is not the satellite's actual translation.

    **The values are OQ-02 and are not seeded.** The shape is here; the numbers come from
    RF engineering.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)

    satellite = models.ForeignKey(
        "inventory.Satellite", on_delete=models.PROTECT, related_name="payload_paths"
    )
    direction = models.CharField(max_length=3, choices=Direction.choices)

    uplink_window = models.ForeignKey(
        FrequencyWindow, on_delete=models.PROTECT, related_name="payload_paths_as_uplink"
    )
    downlink_window = models.ForeignKey(
        FrequencyWindow, on_delete=models.PROTECT, related_name="payload_paths_as_downlink"
    )
    # Denormalised from the windows so a composite foreign key can prove they match the
    # direction. Written by the service, never by a form; the CHECK below and the
    # composite FKs added in the migration make a wrong value impossible rather than
    # merely unlikely (docs/design/04 section 3.2).
    uplink_window_side = models.CharField(max_length=16, choices=SpectrumLeg.choices)
    downlink_window_side = models.CharField(max_length=16, choices=SpectrumLeg.choices)

    translation_method = models.CharField(max_length=16, choices=TranslationMethod.choices)
    translation_constant_hz = models.BigIntegerField(
        help_text=(
            "Offset for OFFSET_ADD/OFFSET_SUBTRACT, or the reflection constant for LO_REFLECT."
        )
    )
    spectral_inversion = models.BooleanField(default=False)

    engineering_reference = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "payload_path"
        ordering = ["satellite__code", "direction", "code"]
        default_permissions = ("view",)
        constraints = [
            models.UniqueConstraint(
                fields=["satellite", "code", "version_number"],
                name="uq_path_satellite_code_version",
            ),
            models.UniqueConstraint(
                fields=["version_group", "version_number"], name="uq_path_group_version"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_path_effective_period",
            ),
            # Specification section 20 lists "Frequency Window side consistency" as a
            # required check. A FWD path runs hub uplink to remote downlink; a RTN path
            # runs remote uplink to hub downlink (A-03). Anything else is not a payload
            # path, and the database refuses it.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        direction=Direction.FWD,
                        uplink_window_side=SpectrumLeg.HUB_UPLINK,
                        downlink_window_side=SpectrumLeg.REMOTE_DOWNLINK,
                    )
                    | models.Q(
                        direction=Direction.RTN,
                        uplink_window_side=SpectrumLeg.REMOTE_UPLINK,
                        downlink_window_side=SpectrumLeg.HUB_DOWNLINK,
                    )
                ),
                name="ck_path_direction_sides",
            ),
            models.CheckConstraint(
                condition=~models.Q(uplink_window=models.F("downlink_window")),
                name="ck_path_windows_differ",
            ),
            ExclusionConstraint(
                name="excl_path_version_overlap",
                expressions=[
                    ("version_group", RangeOperators.EQUAL),
                    ("effective_period", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(is_active=True),
            ),
        ]
        indexes = [
            models.Index(
                fields=["satellite", "direction"],
                name="path_lookup_idx",
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} v{self.version_number} ({self.direction})"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:payload-path-detail", kwargs={"pk": self.pk})

    @property
    def is_inverting(self) -> bool:
        """Does this path invert the spectrum?

        ``LO_REFLECT`` (``f(x) = K - x``) inverts by construction. The stored flag may also
        be set for a path that inverts for another reason, so the two are combined rather
        than one being derived from the other.
        """
        return self.translation_method == TranslationMethod.LO_REFLECT or self.spectral_inversion


class PayloadPolarizationMapping(models.Model):
    """An uplink/downlink polarization pair a Payload Path permits. Section 13.7.

    **The table ships empty.** Which mappings are allowed is **OQ-03**, and a plausible
    default — RHCP up, RHCP down, say — would be indistinguishable from a confirmed one
    once loaded.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payload_path = models.ForeignKey(
        PayloadPath, on_delete=models.CASCADE, related_name="polarization_mappings"
    )
    uplink_polarization = models.CharField(max_length=4, choices=PolarizationType.choices)
    downlink_polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    class Meta:
        db_table = "payload_polarization_mapping"
        ordering = ["uplink_polarization", "downlink_polarization"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["payload_path", "uplink_polarization", "downlink_polarization"],
                name="uq_payload_polarization_mapping",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.uplink_polarization} -> {self.downlink_polarization}"


__all__ = [
    "DateTimeRangeField",
    "FrequencyWindow",
    "GuardPolicy",
    "PayloadPath",
    "PayloadPolarizationMapping",
]
