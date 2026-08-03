"""Specification Dictionary — admin-managed metadata for technical fields.

Specification section 2. One row per technical field displayed anywhere in the product.
The dictionary exists so that a description is written once and rendered everywhere,
rather than being copied into each template: *"Do not hard-code the same specification
description independently in multiple templates."*
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator
from django.db import models

from specifications.registry import DataType, DirectionApplicability


class SpecificationCategory(models.Model):
    """Grouping for related specifications.

    A table rather than a choices enum because section 2 lists Category alongside
    display order as admin-managed metadata: an administrator must be able to add a
    grouping without a code change.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "specification_category"
        verbose_name = "Specification category"
        verbose_name_plural = "Specification categories"
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return self.name


class SpecificationDefinitionQuerySet(models.QuerySet["SpecificationDefinition"]):
    def active(self) -> SpecificationDefinitionQuerySet:
        return self.filter(is_active=True)

    def for_table(self) -> SpecificationDefinitionQuerySet:
        return self.active().filter(visible_in_tables=True)

    def for_form(self) -> SpecificationDefinitionQuerySet:
        return self.active().filter(visible_in_forms=True)

    def for_detail(self) -> SpecificationDefinitionQuerySet:
        return self.active().filter(visible_in_detail=True)

    def for_direction(self, direction: str) -> SpecificationDefinitionQuerySet:
        """Specifications applicable to a payload direction, plus those applying to both."""
        return self.filter(direction_applicability__in=[direction, DirectionApplicability.BOTH])


class SpecificationDefinition(models.Model):
    """One technical field's presentation metadata.

    The split that matters: ``code`` is semantic and referenced by application logic,
    everything else is presentation and is freely editable by an administrator. A
    system-managed row refuses to have its code changed — see :meth:`clean` and
    ``specifications/registry.py``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # --- Identity -----------------------------------------------------------
    code = models.CharField(
        max_length=100,
        unique=True,
        help_text=(
            "Stable internal code referenced by application logic. Cannot be changed "
            "once the code is used by the calculation engine."
        ),
    )
    is_system_managed = models.BooleanField(
        default=False,
        help_text=(
            "Set for codes the application refers to by name. The code of such a row is "
            "read-only; all of its other attributes remain editable."
        ),
    )

    # --- Presentation (all admin-editable) ----------------------------------
    display_name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=60, blank=True)
    description = models.TextField(
        blank=True,
        help_text="Full explanation shown in the information popover.",
    )
    help_text = models.TextField(blank=True, help_text="Guidance shown beside the field in forms.")
    unit = models.CharField(
        max_length=40,
        blank=True,
        help_text="Unit as presented to the user, for example MHz or symbols/second.",
    )
    display_precision = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(12)],
        help_text="Decimal places used when the value is displayed.",
    )

    # --- Classification -----------------------------------------------------
    category = models.ForeignKey(
        SpecificationCategory,
        on_delete=models.PROTECT,
        related_name="specifications",
    )
    data_type = models.CharField(max_length=20, choices=[(t.value, t.name) for t in DataType])
    direction_applicability = models.CharField(
        max_length=4,
        choices=[(d.value, d.name) for d in DirectionApplicability],
        default=DirectionApplicability.BOTH,
    )

    # --- Behaviour ----------------------------------------------------------
    is_calculated = models.BooleanField(
        default=False,
        help_text=(
            "Derived by the calculation engine and read-only for operators "
            "(specification section 26.16)."
        ),
    )
    calculation_note = models.TextField(
        blank=True, help_text="Formula or explanation of how the value is derived."
    )
    source_reference = models.TextField(
        blank=True, help_text="Engineering document or standard this definition comes from."
    )

    # --- Visibility ---------------------------------------------------------
    visible_in_tables = models.BooleanField(default=True)
    visible_in_forms = models.BooleanField(default=False)
    visible_in_detail = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    # --- Audit --------------------------------------------------------------
    record_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SpecificationDefinitionQuerySet.as_manager()

    class Meta:
        db_table = "specification_definition"
        verbose_name = "Specification definition"
        verbose_name_plural = "Specification definitions"
        ordering = ["category__display_order", "display_order", "code"]
        indexes = [
            models.Index(fields=["category", "display_order"], name="spec_category_order_idx"),
            models.Index(
                fields=["display_order"],
                name="spec_table_visible_idx",
                condition=models.Q(is_active=True, visible_in_tables=True),
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("specifications:detail", kwargs={"code": self.code})

    @property
    def label(self) -> str:
        """Human-readable name, falling back to the code."""
        return self.display_name or self.code

    @property
    def compact_label(self) -> str:
        """Short form for dense table headers, falling back through name to code."""
        return self.short_name or self.display_name or self.code

    @property
    def unit_suffix(self) -> str:
        """Unit rendered after a value, empty for dimensionless quantities."""
        return f" {self.unit}" if self.unit else ""

    @property
    def has_popover_content(self) -> bool:
        """Is there anything worth showing in an information popover?

        A popover button that opens an empty box is worse than no button: it teaches
        users that the affordance is not worth using.
        """
        return bool(self.description or self.calculation_note or self.unit or self.help_text)

    @property
    def needs_engineering_input(self) -> bool:
        """True when a description has not yet been supplied.

        Surfaced in the admin screen so unanswered `OPEN QUESTION` items stay visible
        rather than silently shipping as blank help text.
        """
        return not self.description.strip()
