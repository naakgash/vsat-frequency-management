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

from django.db import models

from beams.constants import ConfigurationState, Direction, SpectrumLeg, ValidationOutcome
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
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("beams:detail", kwargs={"pk": self.pk})

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
    #: ``beams.validation`` enforces it — narrowing a Beam to a sub-range is **OQ-27** and is
    #: not supported.
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
