"""Shared model behaviour for inventory master data.

All RF values across this module are stored as **integer Hz** in ``BigIntegerField``
columns. This is not over-caution: a Ka-band uplink near 30 GHz is 3.0e10 Hz, which
overflows a 32-bit signed integer, so ``bigint`` is required rather than merely
preferable (ADR-0003).
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import DateTimeRangeField
from django.db import models
from django.db.models import F, Func, Value


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


class MasterDataVersioned(EffectiveDatedModel, DeactivatableModel):
    """Engineering-critical master data that is superseded rather than overwritten.

    Specification section 13.6: *"A Window in operational use is changed through
    versioning, not retroactive overwrite."* Design assumption **A-16** extends the same
    rule to Payload Paths and Equipment Profiles, because an operational record that
    references one of them must keep referencing exactly the definition it was validated
    against.

    Each version is a **separate row with its own UUID**, sharing a ``version_group`` with
    its siblings. Operational records point at a specific version, so history stays exact
    even after the current definition changes.

    ``effective_period`` is a stored generated column rather than an application-maintained
    one: the exclusion constraint that stops two versions being active at once indexes it,
    and a column the application computes could drift from the columns it is derived from.

    It inherits :class:`EffectiveDatedModel` and :class:`DeactivatableModel` because it
    genuinely requires both — the generated column is derived from the effective dates, and
    the exclusion constraint is conditional on ``is_active``. Declaring the dependency
    rather than assuming a sibling mixin supplies it means a model that versions without
    them fails at import instead of at the first ``supersede``. Concrete models inherit
    both again through :class:`InventoryRecord`; Django resolves the diamond to one copy
    of each field.
    """

    version_group = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        help_text="Shared by every version of the same logical record.",
    )
    version_number = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
        help_text="The version this one replaces.",
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

    class Meta:
        abstract = True

    @property
    def is_current(self) -> bool:
        """True when this version has not been superseded and is still open-ended."""
        return self.is_active and self.effective_until is None
