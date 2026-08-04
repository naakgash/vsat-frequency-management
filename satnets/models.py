"""The Satnet: a service network under one Beam, served from one Hub. Section 13.9.

The first entity in the product that sits at the intersection of **two** scope axes, which
is what makes it the slice where **A-17**'s conjunctive rule stops being a design note and
starts refusing requests.
"""

from __future__ import annotations

import uuid

from django.db import models

from calculations.periods import TimePeriod
from inventory.models import EffectiveDatedModel, InventoryRecord


class Satnet(InventoryRecord, EffectiveDatedModel):
    """A service network: one Beam, one Hub, a validity period. Section 13.9.

    **Never re-parented.** ``beam`` and ``hub`` are set at creation and not editable
    afterwards. Moving a Satnet to another Beam would change which spectrum resources its
    allocations compete on (ADR-0018) without touching the allocations themselves — every
    reservation underneath it would silently start being judged against a different pool.
    Changing Beam means a new Satnet.

    **Capacity is computed, never stored** (§16, ADR-0009). Allocated bandwidth, active path
    count and utilisation all come from ``spectrum.selectors``; a column here would be a
    second source of truth for free capacity, which §16 forbids outright.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)

    beam = models.ForeignKey("beams.Beam", on_delete=models.PROTECT, related_name="satnets")
    hub = models.ForeignKey("inventory.Hub", on_delete=models.PROTECT, related_name="satnets")
    #: Denormalised from ``hub.gateway`` and pinned by a composite foreign key in the
    #: migration. Written by the service, never by a form. It exists so that scope filtering
    #: and Gateway-based listing are one join rather than two, and the composite key is what
    #: stops the copy from drifting into a claim about a Gateway the Hub is not at.
    gateway = models.ForeignKey(
        "inventory.Gateway", on_delete=models.PROTECT, related_name="satnets"
    )

    #: Resolution order for a Satnet Path's guards is override -> Satnet -> Window -> system
    #: (ADR-0016). This is the Satnet rung.
    default_guard_policy = models.ForeignKey(
        "inventory.GuardPolicy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="satnets",
    )

    # §13.9's own fields, and nothing beyond them. What service, customer and platform
    # metadata is actually required is **OQ-21**; inventing columns for it now would produce
    # a shape somebody has to migrate away from.
    service_type = models.CharField(max_length=100, blank=True)
    customer = models.CharField(max_length=200, blank=True)
    platform = models.CharField(max_length=200, blank=True)

    description = models.TextField(blank=True)
    technical_notes = models.TextField(blank=True)

    class Meta:
        db_table = "satnet"
        ordering = ["beam__code", "code"]
        default_permissions = ("view",)
        permissions = [("manage_satnets", "Can create, edit and deactivate Satnets")]
        constraints = [
            # Code uniqueness is per Beam (**A-18**, pending OQ-13).
            models.UniqueConstraint(fields=["beam", "code"], name="uq_satnet_beam_code"),
            # Target for the composite foreign key that will pin a Satnet Path to its
            # Satnet's Beam in S11. Created now so that migration does not have to alter a
            # populated table.
            models.UniqueConstraint(fields=["id", "beam"], name="uq_satnet_id_beam"),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gt=models.F("effective_from")),
                name="ck_satnet_effective_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["beam", "hub"],
                name="satnet_scope_idx",
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("satnets:detail", kwargs={"pk": self.pk})

    @property
    def validity(self) -> TimePeriod:
        """This Satnet's validity as a period, for the **OQ-32** containment check."""
        return TimePeriod(self.effective_from, self.effective_until)

    @property
    def accepts_new_paths(self) -> bool:
        """May a Satnet Path be created under this Satnet right now?

        Named on the model because S11 asks the question and the answer is about *this*
        record's state, not about the caller. An inactive Satnet takes no new allocations —
        its existing ones keep their spectrum until they are retired, because deactivating
        the parent is not the same as cancelling the work underneath it.
        """
        return self.is_active and self.beam.is_active
