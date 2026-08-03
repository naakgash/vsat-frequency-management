"""Inventory forms.

Frequencies are entered and displayed in MHz but stored as integer Hz (ADR-0003). The
conversion happens here, once, in ``Decimal`` — never in a template and never in
JavaScript.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, cast

from django import forms
from django.forms import ModelChoiceField

from inventory.models import Band, EquipmentProfile, Gateway, Hub, Satellite

HZ_PER_MHZ = Decimal(1_000_000)


class MegahertzField(forms.DecimalField):
    """A frequency entered in MHz and stored as integer Hz.

    Exact throughout: the value is parsed as ``Decimal``, scaled by an integer, and
    rejected if it is not a whole number of Hz. Specification section 14.1 forbids binary
    floating point for engineering values, and silently rounding sub-Hz input would be a
    quiet way to reintroduce it.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("decimal_places", 6)
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("help_text", "In MHz.")
        super().__init__(**kwargs)

    def clean(self, value: Any) -> int | None:
        decimal_value = super().clean(value)
        if decimal_value is None:
            return None
        try:
            hz = Decimal(decimal_value) * HZ_PER_MHZ
        except (InvalidOperation, TypeError) as exc:
            raise forms.ValidationError("Enter a valid frequency in MHz.") from exc
        if hz != hz.to_integral_value():
            raise forms.ValidationError(
                "This frequency is finer than 1 Hz. Enter a value with at most six decimal places."
            )
        return int(hz)

    def prepare_value(self, value: Any) -> Any:
        """Render a stored Hz value back into MHz for the widget."""
        if value in (None, ""):
            return value
        try:
            return Decimal(value) / HZ_PER_MHZ
        except (InvalidOperation, TypeError):
            return value


class BootstrapModelForm(forms.ModelForm):
    """Applies Bootstrap classes without repeating them on every widget."""

    reason = forms.CharField(
        max_length=500,
        required=False,
        label="Change reason",
        help_text="Recorded in the audit trail.",
        widget=forms.TextInput(attrs={"placeholder": "Why is this change being made?"}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["expected_version"].initial = self.instance.record_version
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select | forms.SelectMultiple):
                widget.attrs.setdefault("class", "form-select")
            elif name != "expected_version":
                widget.attrs.setdefault("class", "form-control")
            if isinstance(widget, forms.DateTimeInput):
                widget.input_type = "datetime-local"

    def model_values(self) -> dict[str, Any]:
        """Cleaned data limited to model fields, ready for the service.

        ``reason`` and ``expected_version`` are form-only: they drive the audit record and
        the optimistic-locking check and must never reach the model. Read from
        ``self._meta.fields`` — the resolved ``ModelFormOptions`` — rather than from the
        subclass's ``Meta``, which this base class does not declare.
        """
        model_fields = set(self._meta.fields or ())
        return {name: value for name, value in self.cleaned_data.items() if name in model_fields}


class SatelliteForm(BootstrapModelForm):
    class Meta:
        model = Satellite
        fields = [
            "code",
            "name",
            "operator",
            "orbital_position",
            "orbit_type",
            "effective_from",
            "effective_until",
            "description",
            "engineering_reference",
        ]


class BandForm(BootstrapModelForm):
    rf_min_hz = MegahertzField(label="RF minimum", help_text="In MHz. Informative only.")
    rf_max_hz = MegahertzField(label="RF maximum", help_text="In MHz. Informative only.")
    tuning_raster_hz = MegahertzField(
        label="Tuning raster",
        required=False,
        help_text="In MHz. Leave empty while unconfirmed (OQ-31).",
    )
    allowed_polarizations = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Allowed polarization types",
        help_text="Select only the types confirmed for this band (OQ-14).",
    )

    class Meta:
        model = Band
        fields = [
            "code",
            "name",
            "rf_min_hz",
            "rf_max_hz",
            "default_display_unit",
            "tuning_raster_hz",
            "description",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from inventory.constants import PolarizationType

        cast(
            forms.ChoiceField, self.fields["allowed_polarizations"]
        ).choices = PolarizationType.choices
        if self.instance and self.instance.pk:
            self.fields["allowed_polarizations"].initial = list(
                self.instance.allowed_polarizations.values_list("polarization", flat=True)
            )

    def clean(self) -> dict[str, Any]:
        # ModelForm.clean() is typed as optional, but it only returns None when a
        # subclass chooses to; ours does not.
        cleaned = super().clean() or {}
        low, high = cleaned.get("rf_min_hz"), cleaned.get("rf_max_hz")
        if low is not None and high is not None and low >= high:
            raise forms.ValidationError("The RF minimum must be below the RF maximum.")
        return cleaned


class GatewayForm(BootstrapModelForm):
    class Meta:
        model = Gateway
        fields = [
            "code",
            "name",
            "location",
            "latitude",
            "longitude",
            "time_zone",
            "description",
            "technical_notes",
        ]


class HubForm(BootstrapModelForm):
    class Meta:
        model = Hub
        fields = [
            "code",
            "name",
            "gateway",
            "site",
            "platform",
            "vendor",
            "description",
            "technical_notes",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # A hub cannot be attached to a decommissioned site.
        cast(ModelChoiceField, self.fields["gateway"]).queryset = Gateway.objects.filter(
            is_active=True
        )


class EquipmentProfileForm(BootstrapModelForm):
    rf_min_hz = MegahertzField(label="RF minimum")
    rf_max_hz = MegahertzField(label="RF maximum")
    if_min_hz = MegahertzField(label="IF minimum")
    if_max_hz = MegahertzField(label="IF maximum")
    lo_hz = MegahertzField(label="LO frequency")

    class Meta:
        model = EquipmentProfile
        fields = [
            "code",
            "name",
            "type",
            "band",
            "vendor",
            "model",
            "label",
            "rf_min_hz",
            "rf_max_hz",
            "if_min_hz",
            "if_max_hz",
            "lo_hz",
            "conversion_method",
            "sideband",
            "spectral_inversion",
            "priority",
            "gateway",
            "hub",
            "effective_from",
            "effective_until",
            "engineering_reference",
            "description",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(ModelChoiceField, self.fields["band"]).queryset = Band.objects.filter(is_active=True)
        cast(ModelChoiceField, self.fields["gateway"]).queryset = Gateway.objects.filter(
            is_active=True
        )
        cast(ModelChoiceField, self.fields["hub"]).queryset = Hub.objects.filter(is_active=True)
        self.fields["gateway"].required = False
        self.fields["hub"].required = False

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        from inventory.constants import ConversionMethod, Sideband

        method, sideband = cleaned.get("conversion_method"), cleaned.get("sideband")
        # Mirrors the database CHECK, so the user sees a field-level message rather than
        # an IntegrityError page. The database remains the authority.
        valid_pairs = {
            (ConversionMethod.LO_PLUS_IF, Sideband.LOW_SIDE),
            (ConversionMethod.LO_MINUS_IF, Sideband.HIGH_SIDE),
        }
        if method and sideband and method != ConversionMethod.FIXED_OFFSET:
            if (method, sideband) not in valid_pairs:
                raise forms.ValidationError(
                    "RF = LO + IF requires low-side injection, and RF = LO - IF requires "
                    "high-side injection. The conversion method and the sideband must agree, "
                    "or the IF cannot be derived unambiguously from the RF."
                )

        hub, gateway = cleaned.get("hub"), cleaned.get("gateway")
        if hub and gateway and hub.gateway_id != gateway.pk:
            raise forms.ValidationError(f"{hub} belongs to {hub.gateway}, not {gateway}.")
        return cleaned
