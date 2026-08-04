"""Beam Builder forms — the five wizard steps of specification section 5.

The wizard exists because a Beam has two structurally identical chains and roughly a dozen
interdependent choices. Presenting them as one flat form would let an administrator fill in
a downlink window before choosing the payload path that decides which windows are even
possible.

Each step narrows the next: choosing a satellite narrows the payload paths, choosing a path
fixes the windows entirely (**A-06**), and the path's permitted polarization pairs narrow
the polarization choice. Every one of those narrowings is a query in ``__init__``, not
JavaScript — §11 keeps the backend authoritative.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.forms import ModelChoiceField

from beams.constants import CANONICAL_LEG_DEFAULTS
from beams.models import Beam, BeamDirectionConfig
from inventory.constants import PolarizationType
from inventory.models import (
    Band,
    EquipmentProfile,
    PayloadPath,
    PayloadPolarizationMapping,
    Satellite,
)


class BootstrapForm(forms.ModelForm):
    """Applies Bootstrap classes without repeating them on every widget."""

    reason = forms.CharField(
        max_length=500,
        required=False,
        label="Change reason",
        help_text="Recorded in the audit trail.",
        widget=forms.TextInput(attrs={"placeholder": "Why is this change being made?"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select | forms.SelectMultiple):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")

    def model_values(self) -> dict[str, Any]:
        """Cleaned data limited to model fields. ``reason`` drives the audit record only."""
        model_fields = set(self._meta.fields or ())
        return {name: value for name, value in self.cleaned_data.items() if name in model_fields}


class BeamIdentityForm(BootstrapForm):
    """Step 1 — what this Beam is. Specification section 5.1."""

    class Meta:
        model = Beam
        fields = [
            "code",
            "name",
            "satellite",
            "band",
            "coverage",
            "description",
            "engineering_reference",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # A Beam cannot be built on a decommissioned satellite or a retired band.
        cast(ModelChoiceField, self.fields["satellite"]).queryset = Satellite.objects.filter(
            is_active=True
        )
        cast(ModelChoiceField, self.fields["band"]).queryset = Band.objects.filter(is_active=True)


class DirectionForm(BootstrapForm):
    """Steps 2 and 3 — one direction's chain. Specification sections 5.2, 5.3, 5.4.

    The windows are **not** form fields. **A-06** makes them identical to the payload path's,
    so offering them for selection would offer a choice with exactly one correct answer and
    an infinite number of wrong ones. ``model_values`` derives them.
    """

    equipment_profiles = forms.ModelMultipleChoiceField(
        queryset=EquipmentProfile.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Candidate equipment profiles",
        help_text=(
            "The pool a Satnet Path picks from. Limits per site and model are an open "
            "question (OQ-04); none are supplied by the platform."
        ),
    )

    class Meta:
        model = BeamDirectionConfig
        fields = [
            "is_enabled",
            "payload_path",
            "canonical_leg",
            "uplink_polarization",
            "downlink_polarization",
            "spectral_inversion_override",
            "notes",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        config = self.instance

        # Each choice narrows the next. A path from another satellite, or running the other
        # direction, is not a mistake to be caught later — it is not offered.
        cast(ModelChoiceField, self.fields["payload_path"]).queryset = PayloadPath.objects.filter(
            is_active=True,
            effective_until__isnull=True,
            satellite=config.beam.satellite,
            direction=config.direction,
        ).select_related("uplink_window", "downlink_window")

        cast(
            ModelChoiceField, self.fields["equipment_profiles"]
        ).queryset = EquipmentProfile.objects.filter(
            is_active=True, effective_until__isnull=True, band=config.beam.band
        ).order_by("priority", "code")
        if config.pk:
            self.fields["equipment_profiles"].initial = [
                entry.equipment_profile_id for entry in config.equipment_profiles.all()
            ]

        self.fields["canonical_leg"].help_text = (
            f"Which leg the operator enters the centre frequency on. Default for "
            f"{config.direction} is {CANONICAL_LEG_DEFAULTS[config.direction]} (A-07); which "
            f"is correct per direction is OQ-28."
        )
        self._restrict_polarizations(config)

    def _restrict_polarizations(self, config: BeamDirectionConfig) -> None:
        """Offer only the pairs the chosen payload path permits (§13.7).

        When the path lists none — the expected state while **OQ-03** is open — every type
        stays on offer and ``beams.validation`` records that the choice could not be checked.
        Presenting an empty list would look like a bug rather than a missing answer.
        """
        if not config.payload_path_id:
            return
        permitted = PayloadPolarizationMapping.objects.filter(payload_path=config.payload_path)
        if not permitted.exists():
            return

        uplink = sorted({m.uplink_polarization for m in permitted})
        downlink = sorted({m.downlink_polarization for m in permitted})
        labels = dict(PolarizationType.choices)
        cast(forms.ChoiceField, self.fields["uplink_polarization"]).choices = [
            ("", "---------"),
            *((p, labels[p]) for p in uplink),
        ]
        cast(forms.ChoiceField, self.fields["downlink_polarization"]).choices = [
            ("", "---------"),
            *((p, labels[p]) for p in downlink),
        ]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if not cleaned.get("is_enabled"):
            # A disabled direction needs nothing else. §5.4 makes it a deliberate
            # configuration, so requiring a full chain for a direction that carries no
            # traffic would be asking for data nobody has.
            return cleaned

        if not cleaned.get("payload_path"):
            self.add_error("payload_path", "Required for an enabled direction.")
            return cleaned

        leg = cleaned.get("canonical_leg")
        path = cleaned["payload_path"]
        if leg and leg not in {path.uplink_window_side, path.downlink_window_side}:
            self.add_error(
                "canonical_leg",
                f"This chain runs {path.uplink_window_side} to {path.downlink_window_side}; "
                f"{leg} is not part of it.",
            )
        return cleaned

    def model_values(self) -> dict[str, Any]:
        """Derive the windows from the payload path rather than trusting a field.

        **A-06** requires identity, and the surest way to guarantee identity is to never
        accept the windows as input at all.
        """
        values = super().model_values()
        path = self.cleaned_data.get("payload_path")
        if path is not None:
            values["uplink_window"] = path.uplink_window
            values["downlink_window"] = path.downlink_window
        elif not self.cleaned_data.get("is_enabled"):
            # Clearing the path on a direction being disabled clears its windows too;
            # leaving them would leave a chain half-referenced and PROTECT-locked.
            values["uplink_window"] = None
            values["downlink_window"] = None
        return values

    def equipment_choices(self) -> list[tuple[EquipmentProfile, int]]:
        """The candidate pool, each with the profile's own default priority.

        Per-Beam priority overrides are not offered in the wizard: §5.2 asks for a set, and
        the profile's own ``priority`` already expresses the house order. A Beam that needs
        a different order is a real requirement and a later field, not a guess now.
        """
        return [(profile, profile.priority) for profile in self.cleaned_data["equipment_profiles"]]


#: The wizard's steps, in order. Declared once so the progress indicator, the URLs and the
#: "next" button cannot disagree about how many there are or what they are called.
WIZARD_STEPS = [
    (1, "Identity", "What this Beam is: code, satellite and band."),
    (2, "Forward chain", "Payload path, windows, polarization and equipment for FWD."),
    (3, "Return chain", "The same for RTN, or explicitly disabled."),
    (4, "Validation", "Every rule, with reasons."),
    (5, "Activation", "Turn it on — refused while any enabled direction is invalid."),
]
