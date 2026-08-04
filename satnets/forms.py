"""Satnet forms. §13.9, §26.16.

The Beam and the Hub are bindable on **create** and absent on **edit**: a Satnet is never
re-parented, and the surest way to guarantee that is not to offer the fields.
"""

from __future__ import annotations

from typing import Any, cast

from django import forms
from django.forms import ModelChoiceField

from accounts.types import Actor
from beams.selectors import selectable as selectable_beams
from inventory.models import GuardPolicy, Hub
from inventory.scope import effective_hub_ids
from satnets.models import Satnet
from satnets.scope import granted_beam_ids


class BootstrapMixin:
    fields: Any

    def _style(self) -> None:
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class SatnetCreateForm(BootstrapMixin, forms.ModelForm):
    """Create a Satnet under a Beam and Hub this actor is authorised for.

    The choices are narrowed to granted objects, and the service checks again on save. Both
    are needed and they do different jobs: narrowing is a courtesy that stops an operator
    picking something they will be refused, and the service check is the guarantee — a direct
    POST does not go through this form's querysets.
    """

    class Meta:
        model = Satnet
        fields = [
            "code",
            "name",
            "beam",
            "hub",
            "default_guard_policy",
            "service_type",
            "customer",
            "platform",
            "effective_from",
            "effective_until",
            "description",
            "technical_notes",
        ]
        widgets = {
            "effective_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "effective_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args: Any, actor: Actor | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._style()
        if actor is None:
            return

        beams = selectable_beams(actor)
        hubs = Hub.objects.filter(is_active=True).select_related("gateway")
        if not (getattr(actor, "is_superuser", False) or actor.has_perm("accounts.manage_scopes")):
            beams = beams.filter(pk__in=granted_beam_ids(actor))
            hubs = hubs.filter(pk__in=effective_hub_ids(actor))

        cast(ModelChoiceField, self.fields["beam"]).queryset = beams
        cast(ModelChoiceField, self.fields["hub"]).queryset = hubs
        cast(
            ModelChoiceField, self.fields["default_guard_policy"]
        ).queryset = GuardPolicy.objects.filter(is_active=True)
        self.fields["default_guard_policy"].help_text = (
            "Optional. Used when a Satnet Path does not override it (ADR-0016). Guard values "
            "are OQ-07 and nothing is seeded."
        )


class SatnetEditForm(SatnetCreateForm):
    """Everything except the Beam and the Hub."""

    class Meta(SatnetCreateForm.Meta):
        fields = [field for field in SatnetCreateForm.Meta.fields if field not in {"beam", "hub"}]
