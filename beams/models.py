"""The Beam and its two direction chains. Specification section 5.

§5 makes the Beam the **root spectrum pool** with exactly two direction chains. Two shapes
were considered and the choice is recorded in ADR-0004 and docs/design/02 §3:

* flatten both chains onto ``Beam`` as some twenty ``fwd_*`` / ``rtn_*`` columns; or
* a ``BeamDirectionConfig`` child row per direction.

The child row wins. The two chains are structurally identical — window, payload path,
window, equipment, polarization — a direction can be explicitly disabled (§5.4), and
validation state is *per direction*: §26.6 says a Beam cannot be activated while its
mandatory FWD or RTN configuration is invalid. Flattening would duplicate every validation
rule and every uniqueness rule twice, and the two copies would eventually disagree.
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
from django.utils import timezone

from beams.constants import ConfigurationState, Direction, SpectrumLeg, ValidationOutcome
from calculations.periods import TimePeriod
from inventory.constants import PolarizationType
from inventory.models import TimestampedModel


class Beam(TimestampedModel):
    """The root spectrum pool. Specification sections 5.1 and 10.1.

    Not master-data versioned (**A-16**). A Beam is an operational configuration rather than
    an engineering definition consumed by a calculation: what a Satnet Path is validated
    against is the *Frequency Window* and the *Payload Path*, both of which are versioned.
    Optimistic locking plus the audit trail is the right weight here.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)

    satellite = models.ForeignKey(
        "inventory.Satellite", on_delete=models.PROTECT, related_name="beams"
    )
    band = models.ForeignKey("inventory.Band", on_delete=models.PROTECT, related_name="beams")
    coverage = models.CharField(max_length=200, blank=True, help_text="Coverage area or spot name.")

    #: Cached result of the last validation run. Cached, not authoritative: activation
    #: re-validates rather than trusting this column, because the master data underneath it
    #: can be superseded between the two. It exists so a list of fifty Beams does not run
    #: fifty validations to render a badge.
    configuration_state = models.CharField(
        max_length=12,
        choices=ConfigurationState.choices,
        default=ConfigurationState.INCOMPLETE,
    )

    #: The Beam's validity period, half-open ``[effective_from, effective_until)`` (**A-10**).
    #: Added by the **OQ-32** answer, which requires a Satnet Path to be contained within
    #: *"its Beam's validity period"* — a period the Beam did not have. ``docs/design/02`` had
    #: listed Beam among the effective-dated entities and ``docs/design/04`` named
    #: ``ck_beam_effective`` since the design pass; S8 simply never built it, and nothing
    #: needed it badly enough to notice until an allocation had to sit inside it.
    #:
    #: Distinct from ``is_active``, and both are needed. ``is_active`` is an operational
    #: switch somebody flips now; this is the span over which the Beam is a real thing. A Beam
    #: can be within its validity and switched off, and an allocation must respect both.
    effective_from = models.DateTimeField(default=timezone.now)
    effective_until = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty for an open-ended Beam."
    )

    is_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    description = models.TextField(blank=True)
    engineering_reference = models.TextField(blank=True)

    class Meta:
        db_table = "beam"
        ordering = ["code"]
        default_permissions = ("view",)
        permissions = [("manage_beams", "Can configure and activate Beams")]
        constraints = [
            # A Beam that is active must have been activated by someone, at some point.
            # Without this an activation could be faked by flipping the boolean directly,
            # and §18's trail would have nothing to show.
            models.CheckConstraint(
                condition=models.Q(is_active=False)
                | models.Q(activated_at__isnull=False, activated_by__isnull=False),
                name="ck_beam_active_has_activation_record",
            ),
            # §26.6. The database's half of the rule: the application refuses activation
            # with an explanation, and this makes the state unreachable by any other route.
            models.CheckConstraint(
                condition=models.Q(is_active=False)
                | models.Q(configuration_state=ConfigurationState.VALID),
                name="ck_beam_active_requires_valid_configuration",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_beam_effective_period",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("beams:detail", kwargs={"pk": self.pk})

    @property
    def validity(self) -> TimePeriod:
        """This Beam's validity as a period, for the **OQ-32** containment check."""
        return TimePeriod(self.effective_from, self.effective_until)

    @property
    def enabled_directions(self) -> list[BeamDirectionConfig]:
        return [c for c in self.direction_configs.all() if c.is_enabled]


class BeamDirectionConfig(TimestampedModel):
    """One direction's chain: window, payload path, window, equipment, polarization.

    Specification sections 5.2, 5.3 and 5.4. One row per ``(beam, direction)``.

    **A direction may be explicitly disabled**, and that is a deliberate business case
    rather than an absence — a receive-only Beam is a real thing. §5.4 requires the disabled
    state to be visible in the interface, which is why it is a stored flag and not simply a
    missing row: a missing row means "not configured yet", and the two must not look alike.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beam = models.ForeignKey(Beam, on_delete=models.CASCADE, related_name="direction_configs")
    direction = models.CharField(max_length=3, choices=Direction.choices)

    is_enabled = models.BooleanField(
        default=True,
        help_text="A disabled direction is a deliberate configuration, not an omission.",
    )

    #: Pinned to a specific master-data version (**A-16**): a Payload Path superseded later
    #: does not silently re-point this Beam at different translation values.
    payload_path = models.ForeignKey(
        "inventory.PayloadPath",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="beam_direction_configs",
    )
    #: Stored explicitly even though the Payload Path already carries them. §5.2 and §5.3
    #: list the windows *and* the path, and explicit foreign keys give query stability and a
    #: clear audit record. **A-06** requires them to be *identical* to the path's windows and
    #: ``beams.validation`` enforces it. Since OQ-27 was answered these are the *ceiling*
    #: rather than the allocation: what the Beam may actually use is its
    #: :class:`BeamSpectrumAssignment` rows, which carve these windows and carry their own
    #: effective periods (ADR-0019).
    uplink_window = models.ForeignKey(
        "inventory.FrequencyWindow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="beam_configs_as_uplink",
    )
    downlink_window = models.ForeignKey(
        "inventory.FrequencyWindow",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="beam_configs_as_downlink",
    )

    #: Which leg the operator enters the centre frequency on (**A-07**, **OQ-28**).
    #: Configuration rather than code, so changing it for one Beam is an edit.
    canonical_leg = models.CharField(max_length=16, choices=SpectrumLeg.choices, blank=True)

    uplink_polarization = models.CharField(
        max_length=4, choices=PolarizationType.choices, blank=True
    )
    downlink_polarization = models.CharField(
        max_length=4, choices=PolarizationType.choices, blank=True
    )

    #: NULL means "inherit from the Payload Path". A tri-state rather than a boolean,
    #: because "not overridden" and "overridden to false" are different statements and only
    #: one of them survives a change to the path.
    spectral_inversion_override = models.BooleanField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        db_table = "beam_direction_config"
        ordering = ["beam__code", "direction"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["beam", "direction"], name="uq_beam_direction"),
            # A chain is all three references or none of them. A direction holding a payload
            # path but no windows is not a partly-finished chain — it is a chain whose
            # windows disagree with its path by construction, which A-06 forbids outright.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payload_path__isnull=True,
                        uplink_window__isnull=True,
                        downlink_window__isnull=True,
                    )
                    | models.Q(
                        payload_path__isnull=False,
                        uplink_window__isnull=False,
                        downlink_window__isnull=False,
                    )
                ),
                name="ck_beam_direction_chain_all_or_nothing",
            ),
        ]
        # The rule that an *enabled* direction must be configured before its Beam may be
        # activated is deliberately NOT here. It spans two tables — the enabled flag lives on
        # this row, the activation on the Beam — and a CHECK constraint is per-row and cannot
        # join. Django says so directly: models.E041.
        #
        # So the database enforces the *consequence* instead, on the Beam itself:
        # ck_beam_active_requires_valid_configuration. That is the stronger placement anyway.
        # It makes the state §26.6 forbids unreachable by any route, including a direct SQL
        # update, whereas a constraint on this table could only have caught one way of
        # arriving there. beams.validation reports the precondition with reasons; the Beam's
        # CHECK is what makes the outcome impossible.

    def __str__(self) -> str:
        state = "enabled" if self.is_enabled else "disabled"
        return f"{self.beam.code} {self.direction} ({state})"

    @property
    def is_configured(self) -> bool:
        """Are the three references that make a chain present?

        Polarizations and the canonical leg are checked by ``beams.validation`` rather than
        here: they are *correctness* questions with answers that depend on master data,
        while this is the structural question of whether there is anything to validate.
        """
        return all((self.payload_path_id, self.uplink_window_id, self.downlink_window_id))

    @property
    def inverts(self) -> bool:
        """Does this direction invert the spectrum?

        The override wins when it is set; otherwise the Payload Path decides. Resolved here
        so no screen has to remember the precedence.
        """
        if self.spectral_inversion_override is not None:
            return self.spectral_inversion_override
        return bool(self.payload_path and self.payload_path.is_inverting)


class BeamSpectrumAssignment(TimestampedModel):
    """A sub-range of one of a direction's Frequency Windows, for a period. ADR-0019.

    **OQ-27's answer.** The Frequency Window is the *maximum payload capability*; what a Beam
    may actually use is the set of its **active** assignments:

        *"A Beam may use one or more sub-ranges of its Payload Path Frequency Window… The
        free-capacity engine shall calculate available capacity only within the active Beam
        assignments and not across the complete Payload Path Window."*

    Every allocation must be contained in an active assignment **in frequency and in time**.
    Two-dimensional containment is easy to half-implement — check the RF, forget the period,
    and an allocation is valid today and silently outside its assignment next month — so
    :func:`beams.selectors.assignment_covering` resolves both together and returns what it
    matched rather than letting each caller re-derive it.

    The fixed-HTS case is one assignment equal to the whole window, open-ended, and that is
    what configuring a direction creates. It is the degenerate case of the general model, not
    a separate mode, which is why S8's behaviour survives this change untouched (**A-24**).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    direction_config = models.ForeignKey(
        BeamDirectionConfig, on_delete=models.CASCADE, related_name="spectrum_assignments"
    )
    frequency_window = models.ForeignKey(
        "inventory.FrequencyWindow", on_delete=models.PROTECT, related_name="beam_assignments"
    )
    #: The payload configuration this assignment was drawn against. OQ-27 requires the
    #: association, and a Payload Path version *is* the versioned record of one (**A-16**),
    #: so superseding the path cannot silently re-point an assignment at a different payload.
    payload_path = models.ForeignKey(
        "inventory.PayloadPath", on_delete=models.PROTECT, related_name="beam_assignments"
    )

    rf_start_hz = models.BigIntegerField()
    rf_end_hz = models.BigIntegerField(help_text="Exclusive upper edge: the range is [start, end).")
    #: Denormalised from the window and pinned by a composite foreign key added in the
    #: migration. Written by the service, never by a form: it exists so that
    #: ``assignment ⊆ window`` is a per-row CHECK, which a direct SQL update cannot avoid.
    window_rf_start_hz = models.BigIntegerField()
    window_rf_end_hz = models.BigIntegerField()

    rf_range = models.GeneratedField(
        expression=Func(
            F("rf_start_hz"),
            F("rf_end_hz"),
            Value("[)"),
            function="int8range",
            output_field=BigIntegerRangeField(),
        ),
        output_field=BigIntegerRangeField(),
        db_persist=True,
    )
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty for an open-ended assignment."
    )
    effective_period = models.GeneratedField(
        expression=Func(
            F("effective_from"),
            F("effective_until"),
            Value("[)"),
            function="tstzrange",
            output_field=DateTimeRangeField(),
        ),
        output_field=DateTimeRangeField(),
        db_persist=True,
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "beam_spectrum_assignment"
        ordering = ["direction_config__beam__code", "rf_start_hz"]
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rf_start_hz__lt=models.F("rf_end_hz")),
                name="ck_assignment_start_lt_end",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_assignment_effective_period",
            ),
            # The rule that makes the Window a ceiling. Checkable per row only because the
            # window's edges are carried here and the composite FK keeps the copy honest.
            models.CheckConstraint(
                condition=models.Q(rf_start_hz__gte=models.F("window_rf_start_hz"))
                & models.Q(rf_end_hz__lte=models.F("window_rf_end_hz")),
                name="ck_assignment_within_window",
            ),
            # Two active assignments overlapping in RF *and* time would leave two answers to
            # "what may this Beam use", and the gap engine would count the shared spectrum
            # twice. One or more sub-ranges, per OQ-27 — but not overlapping ones.
            ExclusionConstraint(
                name="excl_assignment_overlap",
                expressions=[
                    ("direction_config", RangeOperators.EQUAL),
                    ("frequency_window", RangeOperators.EQUAL),
                    ("rf_range", RangeOperators.OVERLAPS),
                    ("effective_period", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.direction_config} {self.rf_start_hz}-{self.rf_end_hz}"

    @property
    def validity(self) -> TimePeriod:
        """This assignment's validity as a period, for the **OQ-32** containment check."""
        return TimePeriod(self.effective_from, self.effective_until)

    @property
    def width_hz(self) -> int:
        return self.rf_end_hz - self.rf_start_hz

    @property
    def is_whole_window(self) -> bool:
        """Does this assignment span its entire window?

        The fixed-HTS default, and worth naming: a screen showing "the whole window" reads
        very differently from one showing two identical numbers.
        """
        return (
            self.rf_start_hz == self.window_rf_start_hz and self.rf_end_hz == self.window_rf_end_hz
        )


class BeamDirectionSpectrumResource(models.Model):
    """A Spectrum Resource one of this direction's legs occupies. ADR-0018.

    The join that replaced *"the Beam is the pool"*. **OQ-25** makes overlap a property of
    the resource an allocation occupies, and *"an allocation may reserve more than one
    spectrum resource"* — so this is many-to-many rather than a foreign key, and a Satnet
    Path writes one occupancy row per resource per leg (**A-23**).

    It is explicit configuration rather than anything derived, because it is what an engineer
    has to be able to inspect and correct. If two Beams ought to compete and do not, the
    answer is wrong *here* — and no amount of reading the exclusion constraint would show it.

    A direction with no resource on an enabled leg fails validation. That is the deliberate
    consequence of the table shipping empty: there is no default that would not be a guess
    about interference.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    direction_config = models.ForeignKey(
        BeamDirectionConfig, on_delete=models.CASCADE, related_name="spectrum_resources"
    )
    spectrum_resource = models.ForeignKey(
        "inventory.SpectrumResource", on_delete=models.PROTECT, related_name="beam_directions"
    )

    class Meta:
        db_table = "beam_direction_spectrum_resource"
        ordering = ["spectrum_resource__leg", "spectrum_resource__code"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["direction_config", "spectrum_resource"],
                name="uq_beam_direction_spectrum_resource",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.direction_config} -> {self.spectrum_resource}"


class BeamDirectionEquipmentProfile(models.Model):
    """One Equipment Profile a direction may use, with its rank. Sections 5.2 and 5.3.

    §5.2 and §5.3 say "profile **or** profile set", so the set is modelled and a single
    profile is the degenerate case of a set with one member. This is the **candidate pool**
    the Satnet Path picks from in S11 (**A-05**); the wizard only asks an operator to choose
    when more than one candidate is still valid for their placement (§9.2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    direction_config = models.ForeignKey(
        BeamDirectionConfig, on_delete=models.CASCADE, related_name="equipment_profiles"
    )
    equipment_profile = models.ForeignKey(
        "inventory.EquipmentProfile", on_delete=models.PROTECT, related_name="beam_directions"
    )
    priority = models.PositiveIntegerField(
        default=100, help_text="Lower sorts first when several profiles remain valid."
    )

    class Meta:
        db_table = "beam_direction_equipment_profile"
        ordering = ["priority", "equipment_profile__code"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["direction_config", "equipment_profile"],
                name="uq_beam_direction_equipment",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.equipment_profile.code} @ {self.priority}"


class BeamValidationResult(models.Model):
    """One Beam Builder validation run, kept. Sections 10.1 and 18.

    Append-only, like the audit trail and for the same reason: it is the evidence behind an
    activation. §18 requires the trail, and "the Beam was valid when we activated it" is only
    a claim unless the run that said so still exists.

    ``findings`` holds the individual rule results as JSON rather than as rows. They are read
    as a set, never queried across, and a rule added in a later slice would otherwise need a
    migration to store its own result.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    beam = models.ForeignKey(Beam, on_delete=models.CASCADE, related_name="validation_results")
    ran_at = models.DateTimeField(auto_now_add=True)
    ran_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    outcome = models.CharField(max_length=20, choices=ValidationOutcome.choices)
    configuration_state = models.CharField(max_length=12, choices=ConfigurationState.choices)
    findings = models.JSONField(default=list)
    #: True when this run was the immediate justification for an activation.
    activated = models.BooleanField(default=False)

    class Meta:
        db_table = "beam_validation_result"
        ordering = ["-ran_at"]
        default_permissions = ()
        indexes = [models.Index(fields=["beam", "-ran_at"], name="beam_validation_recent_idx")]

    def __str__(self) -> str:
        return f"{self.beam.code} {self.outcome} at {self.ran_at:%Y-%m-%d %H:%M}"

    @property
    def blocking_findings(self) -> list[dict[str, str]]:
        return [f for f in self.findings if f.get("severity") == "ERROR"]
