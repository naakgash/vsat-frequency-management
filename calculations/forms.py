"""The Engineering Preview form.

The only Django in this package that touches the engine's inputs. Everything below —
``bandwidth``, ``guards``, ``ranges``, ``rounding``, ``types``, ``validation`` — imports
nothing but the standard library, which is what an import-linter contract asserts and what
lets the property tests run without a database.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms

from calculations import units
from calculations.types import (
    BandwidthRequest,
    GuardMode,
    GuardPolicySpec,
    GuardSource,
    GuardWidths,
)


class MegahertzField(forms.DecimalField):
    """A frequency entered in MHz, cleaned to integer Hz.

    Deliberately a second, smaller implementation of the idea in
    ``inventory.forms.MegahertzField`` rather than an import of it: ``calculations`` sits
    *below* ``inventory``, so importing it would invert the dependency direction. The
    arithmetic itself is not duplicated — both delegate to :mod:`calculations.units`, which
    is the part that must not drift.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("decimal_places", 6)
        kwargs.setdefault("max_digits", 18)
        super().__init__(**kwargs)

    def clean(self, value: Any) -> int | None:
        decimal_value = super().clean(value)
        if decimal_value is None:
            return None
        try:
            return units.to_hz(decimal_value)
        except units.SubHertzError as exc:
            raise forms.ValidationError(
                "This frequency is finer than 1 Hz. Enter a value with at most six decimal places."
            ) from exc


class PreviewForm(forms.Form):
    """Exercise the engine end to end, before any Beam or Satnet Path exists.

    Section 9.2 offers two entry modes and states that only one of the two is editable at a
    time; this form enforces that rather than deriving both and hoping they agree. Which
    one is pre-selected is **OQ-05**, so neither is defaulted here.
    """

    ENTRY_SYMBOL_RATE = "SYMBOL_RATE"
    ENTRY_OCCUPIED = "OCCUPIED"
    ENTRY_MODES = [
        (ENTRY_SYMBOL_RATE, "Symbol rate (occupied bandwidth is derived)"),
        (ENTRY_OCCUPIED, "Occupied bandwidth (symbol rate is derived)"),
    ]

    entry_mode = forms.ChoiceField(
        choices=ENTRY_MODES,
        initial=ENTRY_SYMBOL_RATE,
        label="Entry mode",
        help_text="Section 9.2 requires both. Which one is pre-selected is OQ-05.",
    )
    symbol_rate_sps = forms.IntegerField(
        required=False,
        min_value=1,
        label="Symbol rate",
        help_text="In symbols per second, as a whole number.",
    )
    occupied_bandwidth_hz = MegahertzField(
        required=False,
        min_value=Decimal("0.000001"),
        label="Occupied bandwidth",
        help_text="In MHz.",
    )
    rolloff = forms.DecimalField(
        min_value=Decimal(0),
        max_value=Decimal(1),
        decimal_places=4,
        label="Roll-off factor",
        help_text="A factor between 0 and 1 — enter 35% as 0.35. Defaults per platform are OQ-06.",
    )
    centre_hz = MegahertzField(label="Centre frequency", help_text="In MHz.")

    guard_mode = forms.ChoiceField(
        choices=[("", "No guard policy")]
        + [(m.value, m.name.title().replace("_", " ")) for m in GuardMode],
        required=False,
        label="Guard mode",
        help_text="No values are supplied by the platform; guard widths are OQ-07.",
    )
    guard_fixed_left_hz = MegahertzField(required=False, label="Fixed left guard")
    guard_fixed_right_hz = MegahertzField(required=False, label="Fixed right guard")
    guard_percent_left = forms.DecimalField(
        required=False, min_value=Decimal(0), decimal_places=3, label="Percent left"
    )
    guard_percent_right = forms.DecimalField(
        required=False, min_value=Decimal(0), decimal_places=3, label="Percent right"
    )

    window_start_hz = MegahertzField(required=False, label="Window start")
    window_end_hz = MegahertzField(required=False, label="Window end")
    min_edge_guard_hz = MegahertzField(required=False, label="Window minimum edge guard")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")
            if name.startswith("guard_") or name.startswith("window_"):
                widget.attrs.setdefault("autocomplete", "off")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        mode = cleaned.get("entry_mode")

        # Exactly one input, per section 9.2. Clearing the unused one rather than merely
        # ignoring it means the value the engine receives is the value the operator can
        # see was used.
        if mode == self.ENTRY_SYMBOL_RATE:
            cleaned["occupied_bandwidth_hz"] = None
            if not cleaned.get("symbol_rate_sps"):
                self.add_error("symbol_rate_sps", "Required in this entry mode.")
        elif mode == self.ENTRY_OCCUPIED:
            cleaned["symbol_rate_sps"] = None
            if not cleaned.get("occupied_bandwidth_hz"):
                self.add_error("occupied_bandwidth_hz", "Required in this entry mode.")

        start, end = cleaned.get("window_start_hz"), cleaned.get("window_end_hz")
        if (start is None) != (end is None):
            self.add_error("window_end_hz", "Give both edges of the Frequency Window, or neither.")
        elif start is not None and end is not None and start >= end:
            self.add_error("window_end_hz", "The window start must be below its end.")

        self._clean_guard(cleaned)
        return cleaned

    def _clean_guard(self, cleaned: dict[str, Any]) -> None:
        """Mirror the mode/value rule that GuardPolicySpec and the database both enforce."""
        mode = cleaned.get("guard_mode")
        if not mode:
            return
        required: dict[str, tuple[str, ...]] = {
            GuardMode.FIXED: ("guard_fixed_left_hz", "guard_fixed_right_hz"),
            GuardMode.PERCENT_OF_OCCUPIED: ("guard_percent_left", "guard_percent_right"),
            GuardMode.MAX_OF_FIXED_AND_PERCENT: (
                "guard_fixed_left_hz",
                "guard_fixed_right_hz",
                "guard_percent_left",
                "guard_percent_right",
            ),
        }
        for field in required.get(str(mode), ()):
            if cleaned.get(field) is None:
                self.add_error(field, "Required for the selected guard mode.")

    # -- translation into engine inputs -------------------------------------
    def bandwidth_request(self) -> BandwidthRequest:
        return BandwidthRequest(
            rolloff=self.cleaned_data["rolloff"],
            symbol_rate_sps=self.cleaned_data.get("symbol_rate_sps"),
            occupied_bandwidth_hz=self.cleaned_data.get("occupied_bandwidth_hz"),
        )

    def guard_policy(self) -> GuardPolicySpec | None:
        mode = self.cleaned_data.get("guard_mode")
        if not mode:
            return None
        return GuardPolicySpec(
            mode=GuardMode(mode),
            # A preview has no Satnet and no Window to inherit from, so the policy is
            # always the operator's own. Labelling it anything else would misreport where
            # a real allocation's guard came from.
            source=GuardSource.OVERRIDE,
            label="Preview policy",
            fixed_left_hz=self.cleaned_data.get("guard_fixed_left_hz"),
            fixed_right_hz=self.cleaned_data.get("guard_fixed_right_hz"),
            percent_left=self.cleaned_data.get("guard_percent_left"),
            percent_right=self.cleaned_data.get("guard_percent_right"),
        )

    def window_bounds(self) -> tuple[int, int] | None:
        start = self.cleaned_data.get("window_start_hz")
        end = self.cleaned_data.get("window_end_hz")
        if start is None or end is None:
            return None
        return start, end


NO_GUARD_WIDTHS = GuardWidths(left_hz=0, right_hz=0, source=GuardSource.NONE)
