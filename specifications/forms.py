"""Specification Dictionary forms."""

from __future__ import annotations

from typing import Any

from django import forms

from specifications.models import SpecificationDefinition
from specifications.services import EDITABLE_FIELDS


class SpecificationForm(forms.ModelForm):
    """Edit a specification's presentation metadata.

    ``code`` is absent from ``fields`` rather than merely disabled. A disabled widget is
    a client-side affordance; omitting the field means a crafted POST containing ``code``
    is ignored by the form entirely, which is the enforcement specification section 12
    asks for.

    ``category``, ``data_type``, ``direction_applicability`` and ``is_calculated`` are
    likewise absent: they are semantic classification that the calculation engine relies
    on, not presentation.
    """

    reason = forms.CharField(
        max_length=500,
        required=False,
        label="Change reason",
        help_text="Recorded in the audit trail.",
        widget=forms.TextInput(attrs={"placeholder": "Why is this change being made?"}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput, required=True)

    class Meta:
        model = SpecificationDefinition
        fields = list(EDITABLE_FIELDS)
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "help_text": forms.Textarea(attrs={"rows": 2}),
            "calculation_note": forms.Textarea(attrs={"rows": 2}),
            "source_reference": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "display_name": "Display name",
            "short_name": "Short name",
            "description": "Full description",
            "help_text": "Help text",
            "unit": "Unit",
            "display_precision": "Display precision",
            "calculation_note": "Calculation or formula",
            "source_reference": "Source or reference",
            "visible_in_tables": "Visible in tables",
            "visible_in_forms": "Visible in forms",
            "visible_in_detail": "Visible in detail views",
            "display_order": "Display order",
            "is_active": "Active",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["expected_version"].initial = self.instance.record_version

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            elif name != "expected_version":
                field.widget.attrs.setdefault("class", "form-control")

    def changed_values(self) -> dict[str, Any]:
        """Only the editable fields, ready to hand to the service."""
        return {field: self.cleaned_data[field] for field in EDITABLE_FIELDS}
