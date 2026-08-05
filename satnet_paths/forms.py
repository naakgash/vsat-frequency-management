"""Satnet Path forms. §9.2, §26.16.

**The field list is the §26.16 guarantee.** Every derived value — both sides' ranges, the
occupied and allocated bandwidths, the symbol rate, the IF, the Beam — is absent from it, so no
role can bind one. `test_submitted_derived_values_are_ignored` POSTs them anyway and proves
they go nowhere.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.forms import ModelChoiceField

from inventory.models import DecimatorAssignment, Gateway, GuardPolicy
from satnet_paths.constants import InputMode
from satnet_paths.models import SatnetPath


class SatnetPathForm(forms.ModelForm):
    """What an operator actually supplies: a size, a roll-off, a centre and a period."""

    class Meta:
        model = SatnetPath
        #: Deliberately short. Anything the engine computes is missing on purpose.
        fields = [
            "code",
            "direction",
            "input_mode",
            "input_value",
            "rolloff",
            "guard_policy",
            "canonical_center_hz",
            "valid_from",
            "valid_until",
            "gateway",
            "decimator_assignment",
        ]
        widgets = {
            "valid_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "valid_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")

        cast(ModelChoiceField, self.fields["guard_policy"]).queryset = GuardPolicy.objects.filter(
            is_active=True
        )
        self.fields["guard_policy"].help_text = (
            "Optional override. Otherwise the Satnet's policy applies, then the Window's "
            "(ADR-0016). Guard values are OQ-07 and nothing is seeded."
        )
        self.fields["input_value"].help_text = (
            "Occupied bandwidth in Hz, or symbol rate in symbols per second — whichever mode "
            "is selected. §9.2 stores the one you typed; the other is derived."
        )
        self.fields["canonical_center_hz"].label = "Centre frequency (Hz)"

        # **A-28.** The widget is a `datetime-local` input, which shows no zone at all and
        # would otherwise be read as the operator's own. Every value here is UTC, in and out.
        for name in ("valid_from", "valid_until"):
            self.fields[name].label = f"{self.fields[name].label} (UTC)"
            self.fields[name].help_text = "Entered and stored in UTC."

        cast(ModelChoiceField, self.fields["gateway"]).queryset = Gateway.objects.filter(
            is_active=True
        )
        cast(
            ModelChoiceField, self.fields["decimator_assignment"]
        ).queryset = DecimatorAssignment.objects.filter(is_active=True).select_related("decimator")
        self.fields["gateway"].help_text = (
            "A shared reference (A-26). Two allocations through the same Gateway do not "
            "conflict on that account — contention is judged on spectrum resources."
        )
        self.fields["decimator_assignment"].help_text = (
            "The Decimator configuration this Path consumes (A-27). Several Paths may share "
            "one; two overlapping configurations on one Decimator are refused."
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        mode, value = cleaned.get("input_mode"), cleaned.get("input_value")
        # §9.2 forbids the two sizing inputs being independently editable. One mode and one
        # value is the whole of that rule, and the pairing is checked here rather than trusted
        # because a mode without a value produces a bandwidth of zero further downstream.
        if mode and not value:
            self.add_error("input_value", f"A value is required for {InputMode(mode).label}.")
        return cleaned
