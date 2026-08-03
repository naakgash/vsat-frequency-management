"""Inventory views.

Views stay thin: parse, authorise via the service, render. Read is open to every
authenticated role; writes require ``inventory.manage_inventory``.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, TemplateView, View

from accounts.mixins import AuditedPermissionRequiredMixin
from inventory import dependencies, services
from inventory.constants import MANAGE_INVENTORY
from inventory.forms import (
    BandForm,
    EquipmentProfileForm,
    GatewayForm,
    HubForm,
    SatelliteForm,
)
from inventory.models import Band, BandPolarization, EquipmentProfile, Gateway, Hub, Satellite

#: The section 3 split. Independent objects can be created on their own; dependent ones
#: require at least one independent object to exist first. Rendered from this single
#: declaration so the navigation and the index cannot disagree.
INDEPENDENT_ENTITIES = [
    ("Satellites", "inventory:satellite-list", "Orbital assets that carry payloads.", "satellite"),
    ("Bands", "inventory:band-list", "Frequency bands and their permitted polarizations.", "band"),
    ("Gateways", "inventory:gateway-list", "Teleport sites.", "gateway"),
    ("Hubs", "inventory:hub-list", "Baseband platform instances at a Gateway.", "hub"),
    (
        "Equipment Profiles",
        "inventory:equipment-list",
        "BUC, BDC and LNB conversion profiles.",
        "equipment",
    ),
]

DEPENDENT_ENTITIES = [
    ("Frequency Windows", "Allocatable spectrum per Satellite, Band and leg.", "S5"),
    ("Payload Paths", "Satellite translation between an uplink and a downlink window.", "S5"),
    ("Beams", "The root spectrum pool, built from the above.", "S8"),
]


class InventoryIndexView(LoginRequiredMixin, TemplateView):
    """Inventory landing page, visibly split per specification section 3."""

    template_name = "inventory/index.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Counts are resolved here, not in the template: Gateway and Hub are
        # scope-filtered, so an Operator must see the size of their own estate rather
        # than the whole one.
        counts = {
            "satellite": Satellite.objects.count(),
            "band": Band.objects.count(),
            "gateway": Gateway.objects.for_user(user).count(),
            "hub": Hub.objects.for_user(user).count(),
            "equipment": EquipmentProfile.objects.count(),
        }
        context["independent"] = [
            {"label": label, "url_name": url_name, "blurb": blurb, "count": counts[key]}
            for label, url_name, blurb, key in INDEPENDENT_ENTITIES
        ]
        context["dependent"] = [
            {"label": label, "blurb": blurb, "slice": slice_name}
            for label, blurb, slice_name in DEPENDENT_ENTITIES
        ]
        context["can_manage"] = user.has_perm(MANAGE_INVENTORY)
        return context


class InventoryListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    """Shared list behaviour."""

    paginate_by = 50
    context_object_name = "objects"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["can_manage"] = self.request.user.has_perm(MANAGE_INVENTORY)
        return context


class InventoryDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    """Shared detail behaviour, including the section 3.2 dependency summary."""

    context_object_name = "object"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        instance = context["object"]
        context["dependencies"] = dependencies.summarise(instance)
        context["blocking"] = dependencies.blocking_dependencies(instance)
        context["can_manage"] = self.request.user.has_perm(MANAGE_INVENTORY)
        return context


# ---------------------------------------------------------------------------
# Satellites
# ---------------------------------------------------------------------------
class SatelliteListView(InventoryListView):
    permission_required = "inventory.view_satellite"
    template_name = "inventory/satellite_list.html"

    def get_queryset(self) -> QuerySet[Satellite]:
        return Satellite.objects.all()


class SatelliteDetailView(InventoryDetailView):
    permission_required = "inventory.view_satellite"
    template_name = "inventory/satellite_detail.html"
    queryset = Satellite.objects.all()


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------
class BandListView(InventoryListView):
    permission_required = "inventory.view_band"
    template_name = "inventory/band_list.html"

    def get_queryset(self) -> QuerySet[Band]:
        # Explicit ordering: an aggregate annotation adds a GROUP BY that drops
        # Meta.ordering, and paginating an unordered queryset can repeat or skip rows.
        return (
            Band.objects.prefetch_related("allowed_polarizations")
            .annotate(profile_count=Count("equipment_profiles"))
            .order_by("code")
        )


class BandDetailView(InventoryDetailView):
    permission_required = "inventory.view_band"
    template_name = "inventory/band_detail.html"
    queryset = Band.objects.prefetch_related("allowed_polarizations")


# ---------------------------------------------------------------------------
# Gateways and Hubs
# ---------------------------------------------------------------------------
class GatewayListView(InventoryListView):
    permission_required = "inventory.view_gateway"
    template_name = "inventory/gateway_list.html"

    def get_queryset(self) -> QuerySet[Gateway]:
        # Scope-filtered: a non-admin sees only the sites they are granted.
        queryset: QuerySet[Gateway] = Gateway.objects.for_user(self.request.user)
        return queryset.annotate(hub_count=Count("hubs")).order_by("code")


class GatewayDetailView(InventoryDetailView):
    permission_required = "inventory.view_gateway"
    template_name = "inventory/gateway_detail.html"

    def get_queryset(self) -> QuerySet[Gateway]:
        return Gateway.objects.for_user(self.request.user).prefetch_related("hubs")


class HubListView(InventoryListView):
    permission_required = "inventory.view_hub"
    template_name = "inventory/hub_list.html"

    def get_queryset(self) -> QuerySet[Hub]:
        return Hub.objects.for_user(self.request.user).select_related("gateway")


class HubDetailView(InventoryDetailView):
    permission_required = "inventory.view_hub"
    template_name = "inventory/hub_detail.html"

    def get_queryset(self) -> QuerySet[Hub]:
        return Hub.objects.for_user(self.request.user).select_related("gateway")


# ---------------------------------------------------------------------------
# Equipment Profiles
# ---------------------------------------------------------------------------
class EquipmentListView(InventoryListView):
    permission_required = "inventory.view_equipmentprofile"
    template_name = "inventory/equipment_list.html"

    def get_queryset(self) -> QuerySet[EquipmentProfile]:
        return EquipmentProfile.objects.select_related("band", "gateway", "hub")


class EquipmentDetailView(InventoryDetailView):
    permission_required = "inventory.view_equipmentprofile"
    template_name = "inventory/equipment_detail.html"
    queryset = EquipmentProfile.objects.select_related("band", "gateway", "hub")


# ---------------------------------------------------------------------------
# Create / edit / activate — one implementation for all five entities
# ---------------------------------------------------------------------------
class EntityConfig(NamedTuple):
    """How the shared create/edit view handles one inventory entity.

    A named tuple rather than a bare tuple so the call sites read as ``config.label``
    instead of ``config[3]``, and so mypy checks the unpacking.
    """

    model: type[Any]
    form_class: type[Any]
    list_url: str
    label: str


ENTITY_CONFIG: dict[str, EntityConfig] = {
    "satellite": EntityConfig(Satellite, SatelliteForm, "inventory:satellite-list", "Satellite"),
    "band": EntityConfig(Band, BandForm, "inventory:band-list", "Band"),
    "gateway": EntityConfig(Gateway, GatewayForm, "inventory:gateway-list", "Gateway"),
    "hub": EntityConfig(Hub, HubForm, "inventory:hub-list", "Hub"),
    "equipment": EntityConfig(
        EquipmentProfile,
        EquipmentProfileForm,
        "inventory:equipment-list",
        "Equipment Profile",
    ),
}


class InventoryEditView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Create or edit any inventory entity.

    One view rather than ten, because the five entities differ only in their form. The
    per-entity behaviour that does differ — Band's polarization child rows — is handled
    explicitly below rather than by a hook nobody would find.
    """

    permission_required = MANAGE_INVENTORY

    def get(self, request: HttpRequest, entity: str, pk: str | None = None) -> HttpResponse:
        config = self._config(entity)
        instance = get_object_or_404(config.model, pk=pk) if pk else None
        form = config.form_class(instance=instance)
        return self._render(request, entity, config.label, form, instance)

    def post(self, request: HttpRequest, entity: str, pk: str | None = None) -> HttpResponse:
        config = self._config(entity)
        instance = get_object_or_404(config.model, pk=pk) if pk else None
        form = config.form_class(request.POST, instance=instance)

        if not form.is_valid():
            return self._render(request, entity, config.label, form, instance, status=400)

        try:
            if instance is None:
                saved = services.create(
                    actor=request.user,
                    model=config.model,
                    values=form.model_values(),
                    reason=form.cleaned_data.get("reason", ""),
                )
            else:
                saved = services.update(
                    actor=request.user,
                    instance=instance,
                    values=form.model_values(),
                    expected_version=form.cleaned_data.get("expected_version"),
                    reason=form.cleaned_data.get("reason", ""),
                )
        except services.StaleRecordError as exc:
            messages.warning(request, str(exc))
            # Only an edit can go stale; a create has no prior version to conflict with.
            assert instance is not None
            instance.refresh_from_db()
            return self._render(
                request,
                entity,
                config.label,
                config.form_class(instance=instance),
                instance,
                status=409,
            )

        if entity == "band":
            self._sync_band_polarizations(saved, form.cleaned_data.get("allowed_polarizations", []))

        messages.success(request, f"{config.label} {saved} saved.")
        return redirect(saved.get_absolute_url())

    @staticmethod
    def _sync_band_polarizations(band: Band, selected: list[str]) -> None:
        BandPolarization.objects.filter(band=band).exclude(polarization__in=selected).delete()
        existing = set(
            BandPolarization.objects.filter(band=band).values_list("polarization", flat=True)
        )
        BandPolarization.objects.bulk_create(
            [BandPolarization(band=band, polarization=p) for p in selected if p not in existing]
        )

    @staticmethod
    def _config(entity: str) -> EntityConfig:
        if entity not in ENTITY_CONFIG:
            from django.http import Http404

            raise Http404(f"Unknown inventory entity: {entity}")
        return ENTITY_CONFIG[entity]

    def _render(
        self,
        request: HttpRequest,
        entity: str,
        label: str,
        form: Any,
        instance: Any,
        status: int = 200,
    ) -> HttpResponse:
        return render(
            request,
            "inventory/edit.html",
            {"entity": entity, "entity_label": label, "form": form, "object": instance},
            status=status,
        )


class InventoryActivationView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Activate or deactivate an inventory record."""

    permission_required = MANAGE_INVENTORY

    def post(self, request: HttpRequest, entity: str, pk: str, action: str) -> HttpResponse:
        config = InventoryEditView._config(entity)
        instance = get_object_or_404(config.model, pk=pk)

        try:
            services.set_active(
                actor=request.user,
                instance=instance,
                active=(action == "activate"),
                reason=request.POST.get("reason", ""),
            )
        except services.InUseError as exc:
            messages.error(request, str(exc))
            return redirect(instance.get_absolute_url())

        verb = "activated" if action == "activate" else "deactivated"
        messages.success(request, f"{config.label} {instance} {verb}.")
        return redirect(instance.get_absolute_url())
