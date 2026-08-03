"""Independent inventory master data.

Specification sections 3.1 and 13.1 to 13.5. These five entities can be created on their
own; Frequency Windows, Payload Paths and Beams depend on them and arrive in later
slices.

All RF values are stored as **integer Hz** in ``BigIntegerField`` columns. This is not
over-caution: a Ka-band uplink near 30 GHz is 3.0e10 Hz, which overflows a 32-bit signed
integer, so ``bigint`` is required rather than merely preferable (ADR-0003).
"""

from __future__ import annotations

import uuid

from django.db import models

from inventory.constants import (
    ConversionMethod,
    EquipmentType,
    OrbitType,
    PolarizationType,
    Sideband,
)
from inventory.scope import GatewayQuerySet, HubQuerySet


class TimestampedModel(models.Model):
    """Created/updated metadata required of every inventory record (section 13)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Optimistic locking (section 15.5).
    record_version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True


class DeactivatableModel(models.Model):
    """Records that are retired by deactivation rather than deletion.

    Specification section 20 forbids hard-deleting used inventory, so every entity here
    carries the same flag. Declared once so the deactivation service can be typed against
    it instead of against bare ``Model``.
    """

    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class InventoryRecord(TimestampedModel, DeactivatableModel):
    """Everything an inventory master-data record has in common."""

    class Meta:
        abstract = True


class EffectiveDatedModel(models.Model):
    """Half-open effective period ``[effective_from, effective_until)`` (A-10)."""

    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty for an open-ended record."
    )

    class Meta:
        abstract = True


class Satellite(InventoryRecord, EffectiveDatedModel):
    """Specification section 13.1."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    operator = models.CharField(max_length=200, blank=True)
    # Free text rather than a number. For a geostationary satellite this is a longitude
    # such as "42.0E", but MEO and LEO constellations have no single such value, and
    # imposing a numeric column would force a shape the specification does not state.
    orbital_position = models.CharField(
        max_length=50, blank=True, help_text="For example 42.0E for a geostationary satellite."
    )
    orbit_type = models.CharField(max_length=3, choices=OrbitType.choices)
    description = models.TextField(blank=True)
    engineering_reference = models.TextField(
        blank=True, help_text="Engineering or reference document for this satellite."
    )

    class Meta:
        db_table = "satellite"
        ordering = ["code"]
        # Each inventory model gets its own view permission, but writes are governed by a
        # single capability: specification section 12 grants inventory management as one
        # thing, and five separate add/change/delete triples would imply a granularity the
        # product does not offer. It is declared here because a permission needs a model
        # to hang from; it applies to every entity in this module.
        default_permissions = ("view",)
        permissions = [
            ("manage_inventory", "Can create, edit and deactivate inventory master data"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_satellite_effective_period",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:satellite-detail", kwargs={"pk": self.pk})


class Band(InventoryRecord):
    """Specification section 13.2.

    Band limits are **informative**. Actual allocation permission comes from Frequency
    Windows, so nothing validates an allocation against these values; they exist to help
    an operator recognise a mistyped frequency.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    rf_min_hz = models.BigIntegerField(help_text="Informative lower bound, in Hz.")
    rf_max_hz = models.BigIntegerField(help_text="Informative upper bound, in Hz.")
    default_display_unit = models.CharField(max_length=20, default="MHz")
    # OQ-31: whether a minimum tuning step exists, and its size, is unconfirmed. NULL
    # means no raster is enforced; Auto-place emits an informational note rather than
    # silently proposing a centre no modem can tune to.
    tuning_raster_hz = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Minimum centre-frequency step, in Hz. Leave empty when unconfirmed (OQ-31).",
    )
    description = models.TextField(blank=True)

    class Meta:
        db_table = "band"
        ordering = ["code"]
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rf_min_hz__lt=models.F("rf_max_hz")),
                name="ck_band_rf_range",
            ),
            models.CheckConstraint(
                condition=models.Q(tuning_raster_hz__isnull=True)
                | models.Q(tuning_raster_hz__gt=0),
                name="ck_band_tuning_raster_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:band-detail", kwargs={"pk": self.pk})

    @property
    def allowed_polarization_list(self) -> list[str]:
        return [p.get_polarization_display() for p in self.allowed_polarizations.all()]


class BandPolarization(models.Model):
    """A polarization type a Band permits (section 13.2).

    A child table rather than an array column so each entry is referencable and
    admin-editable on its own. **No Band ships with any polarization preselected** —
    which types are in use is OQ-14.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    band = models.ForeignKey(Band, on_delete=models.CASCADE, related_name="allowed_polarizations")
    polarization = models.CharField(max_length=4, choices=PolarizationType.choices)

    class Meta:
        db_table = "band_polarization"
        ordering = ["polarization"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["band", "polarization"], name="uq_band_polarization"),
        ]

    def __str__(self) -> str:
        return f"{self.band.code} / {self.polarization}"


class Gateway(InventoryRecord):
    """Teleport site. Specification section 13.3.

    A Gateway is a **physical site**; a Hub is a baseband platform instance at that site.
    Section 3.1 requires them to stay separate entities, and they are: a Gateway may host
    hubs from different vendors, and a Hub cannot span Gateways.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    time_zone = models.CharField(
        max_length=64, blank=True, help_text="IANA time zone name, for example Europe/Istanbul."
    )
    description = models.TextField(blank=True)
    technical_notes = models.TextField(blank=True)

    objects = GatewayQuerySet.as_manager()

    class Meta:
        db_table = "gateway"
        ordering = ["code"]
        default_permissions = ("view",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__isnull=True)
                | models.Q(latitude__gte=-90, latitude__lte=90),
                name="ck_gateway_latitude_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__isnull=True)
                | models.Q(longitude__gte=-180, longitude__lte=180),
                name="ck_gateway_longitude_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:gateway-detail", kwargs={"pk": self.pk})


class Hub(InventoryRecord):
    """Baseband platform instance at a Gateway. Specification section 13.4."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    # PROTECT, not CASCADE: removing a site must never silently remove the hubs that
    # Satnets depend on (section 20).
    gateway = models.ForeignKey(Gateway, on_delete=models.PROTECT, related_name="hubs")
    site = models.CharField(max_length=200, blank=True)
    platform = models.CharField(max_length=200, blank=True, help_text="Hub system or platform.")
    vendor = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    technical_notes = models.TextField(blank=True)

    objects = HubQuerySet.as_manager()

    class Meta:
        db_table = "hub"
        ordering = ["gateway__code", "code"]
        default_permissions = ("view",)
        constraints = [
            # Code uniqueness is per Gateway (assumption A-18, pending OQ-13).
            models.UniqueConstraint(fields=["gateway", "code"], name="uq_hub_gateway_code"),
            # Target for the composite foreign key that will pin Satnet.gateway_id to its
            # Hub's Gateway (docs/design/04 section 3.2). Created now so the later
            # migration does not have to alter a populated table.
            models.UniqueConstraint(fields=["id", "gateway"], name="uq_hub_id_gateway"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:hub-detail", kwargs={"pk": self.pk})


class EquipmentProfile(InventoryRecord, EffectiveDatedModel):
    """BUC, BDC or LNB conversion profile. Specification section 13.5.

    Carries the version columns of assumption A-16. The versioning *service* arrives in
    S5 alongside Frequency Window and Payload Path so all three share one implementation;
    the columns and the non-overlap constraint ship here so that later migration does not
    have to alter a populated, constraint-bearing table.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=6, choices=EquipmentType.choices)
    band = models.ForeignKey(Band, on_delete=models.PROTECT, related_name="equipment_profiles")
    vendor = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=200, blank=True)

    # --- Conversion algebra (docs/design/02 section 2.3) --------------------
    # Values are OQ-04 and are supplied by RF engineering per site and model. Nothing is
    # seeded.
    rf_min_hz = models.BigIntegerField()
    rf_max_hz = models.BigIntegerField()
    if_min_hz = models.BigIntegerField()
    if_max_hz = models.BigIntegerField()
    lo_hz = models.BigIntegerField(help_text="Local oscillator frequency, in Hz.")
    conversion_method = models.CharField(max_length=14, choices=ConversionMethod.choices)
    sideband = models.CharField(max_length=10, choices=Sideband.choices)
    spectral_inversion = models.BooleanField(default=False)

    priority = models.PositiveIntegerField(
        default=100, help_text="Lower sorts first when several profiles are valid."
    )
    # LOW / MID / HIGH may be used here as labels. They must never drive branching logic
    # (specification section 13.5).
    label = models.CharField(max_length=50, blank=True)

    # Optional applicability (section 13.5).
    gateway = models.ForeignKey(
        Gateway, null=True, blank=True, on_delete=models.PROTECT, related_name="equipment_profiles"
    )
    hub = models.ForeignKey(
        Hub, null=True, blank=True, on_delete=models.PROTECT, related_name="equipment_profiles"
    )

    engineering_reference = models.TextField(blank=True)
    description = models.TextField(blank=True)

    # --- Master-data versioning (A-16) --------------------------------------
    version_group = models.UUIDField(
        default=uuid.uuid4,
        help_text="Shared by every version of the same logical profile.",
    )
    version_number = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    class Meta:
        db_table = "equipment_profile"
        ordering = ["code", "-version_number"]
        default_permissions = ("view",)
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version_number"], name="uq_equipment_code_version"
            ),
            models.UniqueConstraint(
                fields=["version_group", "version_number"], name="uq_equipment_group_version"
            ),
            models.CheckConstraint(
                condition=models.Q(rf_min_hz__lt=models.F("rf_max_hz")),
                name="ck_equipment_rf_range",
            ),
            models.CheckConstraint(
                condition=models.Q(if_min_hz__lt=models.F("if_max_hz")),
                name="ck_equipment_if_range",
            ),
            models.CheckConstraint(
                condition=models.Q(lo_hz__gt=0), name="ck_equipment_lo_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(if_min_hz__gte=0), name="ck_equipment_if_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_equipment_effective_period",
            ),
            # The conversion algebra is only invertible when the method and the sideband
            # agree: low-side injection puts the LO below the RF and does not invert,
            # high-side injection puts it above and does. A profile claiming otherwise
            # would silently produce a wrong IF, so the pairing is enforced rather than
            # trusted.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        conversion_method=ConversionMethod.LO_PLUS_IF, sideband=Sideband.LOW_SIDE
                    )
                    | models.Q(
                        conversion_method=ConversionMethod.LO_MINUS_IF, sideband=Sideband.HIGH_SIDE
                    )
                    | models.Q(conversion_method=ConversionMethod.FIXED_OFFSET)
                ),
                name="ck_equipment_conversion_sideband",
            ),
        ]
        indexes = [
            models.Index(
                fields=["band", "type", "priority"],
                name="equipment_matching_idx",
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} v{self.version_number} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("inventory:equipment-detail", kwargs={"pk": self.pk})

    @property
    def is_inverting(self) -> bool:
        """Does this profile invert the spectrum?

        High-side injection inverts. The stored ``spectral_inversion`` flag may also be
        set for a profile that inverts for another reason, so the two are combined rather
        than one being derived from the other.
        """
        return self.sideband == Sideband.HIGH_SIDE or self.spectral_inversion
