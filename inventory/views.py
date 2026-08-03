"""Inventory views.

Views stay thin: parse, authorise via the service, render. Read is open to every
authenticated role; writes require ``inventory.manage_inventory``.
"""

from __future__ import annotations

from typing import Any, NamedTuple, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.db.models import Count, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, TemplateView, View

from accounts.mixins import AuditedPermissionRequiredMixin
from inventory import dependencies, services, versioning
from inventory.constants import MANAGE_INVENTORY
from inventory.forms import (
    BandForm,
    EquipmentProfileForm,
    FrequencyWindowForm,
    GatewayForm,
    GuardPolicyForm,
    HubForm,
    PayloadPathForm,
    SatelliteForm,
)
from inventory.models import (
    Band,
    BandPolarization,
    EquipmentProfile,
    FrequencyWindow,
    Gateway,
    GuardPolicy,
    Hub,
    MasterDataVersioned,
    PayloadPath,
    Satellite,
)


class EntityLink(NamedTuple):
    """One row of the inventory index.

    ``url_name`` is ``None`` for an entity a later slice delivers, in which case
    ``slice_name`` names that slice. Rendering both cases from one declaration is what
    stops the index and the navigation from disagreeing about what exists.
    """

    label: str
    blurb: str
    url_name: str | None = None
    count_key: str = ""
    slice_name: str = ""


#: The section 3 split. Independent objects can be created on their own; dependent ones
#: require at least one independent object to exist first.
#:
#: A Guard Policy holds no foreign keys, so by section 3's own test it is independent —
#: it is listed here rather than beside the Windows that reference it.
INDEPENDENT_ENTITIES = [
    EntityLink(
        "Satellites", "Orbital assets that carry payloads.", "inventory:satellite-list", "satellite"
    ),
    EntityLink(
        "Bands",
        "Frequency bands and their permitted polarizations.",
        "inventory:band-list",
        "band",
    ),
    EntityLink("Gateways", "Teleport sites.", "inventory:gateway-list", "gateway"),
    EntityLink("Hubs", "Baseband platform instances at a Gateway.", "inventory:hub-list", "hub"),
    EntityLink(
        "Equipment Profiles",
        "BUC, BDC and LNB conversion profiles.",
        "inventory:equipment-list",
        "equipment",
    ),
    EntityLink(
        "Guard Policies",
        "Named separation rules a Window or Satnet defaults to.",
        "inventory:guard-policy-list",
        "guard_policy",
    ),
]

DEPENDENT_ENTITIES = [
    EntityLink(
        "Frequency Windows",
        "Allocatable spectrum per Satellite, Band and leg.",
        "inventory:frequency-window-list",
        "frequency_window",
    ),
    EntityLink(
        "Payload Paths",
        "Satellite translation between an uplink and a downlink window.",
        "inventory:payload-path-list",
        "payload_path",
    ),
    EntityLink("Beams", "The root spectrum pool, built from the above.", slice_name="S8"),
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
            "guard_policy": GuardPolicy.objects.count(),
            # Versioned entities are counted by logical record, not by row: three versions
            # of one window are one window, and reporting "3" would overstate the estate.
            "frequency_window": FrequencyWindow.objects.values("version_group").distinct().count(),
            "payload_path": PayloadPath.objects.values("version_group").distinct().count(),
        }
        context["independent"] = [self._entry(link, counts) for link in INDEPENDENT_ENTITIES]
        context["dependent"] = [self._entry(link, counts) for link in DEPENDENT_ENTITIES]
        context["can_manage"] = user.has_perm(MANAGE_INVENTORY)
        return context

    @staticmethod
    def _entry(link: EntityLink, counts: dict[str, int]) -> dict[str, Any]:
        return {
            "label": link.label,
            "blurb": link.blurb,
            "url_name": link.url_name,
            "count": counts.get(link.count_key),
            "slice": link.slice_name,
        }


class EntityViewMixin:
    """Supplies the entity slug and its list route to every template.

    Passed through the context rather than repeated in each ``{% include ... with %}``:
    a detail screen now includes three partials that all need the slug, and three copies
    of the same literal is three chances for one of them to be wrong.
    """

    #: Key into :data:`ENTITY_CONFIG`, declared by each concrete view.
    entity: str = ""

    def entity_context(self) -> dict[str, Any]:
        config = ENTITY_CONFIG.get(self.entity)
        if config is None:
            return {"entity": self.entity}
        return {
            "entity": self.entity,
            "list_url": config.list_url,
            "list_label": config.label,
        }


class InventoryListView(
    LoginRequiredMixin, AuditedPermissionRequiredMixin, EntityViewMixin, ListView
):
    """Shared list behaviour."""

    paginate_by = 50
    context_object_name = "objects"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(self.entity_context())
        context["can_manage"] = self.request.user.has_perm(MANAGE_INVENTORY)
        return context


class InventoryDetailView(
    LoginRequiredMixin, AuditedPermissionRequiredMixin, EntityViewMixin, DetailView
):
    """Shared detail behaviour, including the section 3.2 dependency summary."""

    context_object_name = "object"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(self.entity_context())
        instance = context["object"]
        context["dependencies"] = dependencies.summarise(instance)
        context["blocking"] = dependencies.blocking_dependencies(instance)
        context["can_manage"] = self.request.user.has_perm(MANAGE_INVENTORY)

        if isinstance(instance, MasterDataVersioned):
            # Which version you are looking at is the first thing to know about a
            # versioned record: reading superseded numbers as current is exactly the
            # mistake section 13.6 exists to prevent.
            context["is_versioned"] = True
            context["is_superseded"] = instance.effective_until is not None
            context["current_version"] = versioning.current_version(instance)
            context["version_count"] = (
                type(instance)._default_manager.filter(version_group=instance.version_group).count()
            )
            context["frozen_by"] = versioning.is_in_operational_use(instance)
        return context


# ---------------------------------------------------------------------------
# Satellites
# ---------------------------------------------------------------------------
class SatelliteListView(InventoryListView):
    entity = "satellite"
    permission_required = "inventory.view_satellite"
    template_name = "inventory/satellite_list.html"

    def get_queryset(self) -> QuerySet[Satellite]:
        return Satellite.objects.all()


class SatelliteDetailView(InventoryDetailView):
    entity = "satellite"
    permission_required = "inventory.view_satellite"
    template_name = "inventory/satellite_detail.html"
    queryset = Satellite.objects.all()


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------
class BandListView(InventoryListView):
    entity = "band"
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
    entity = "band"
    permission_required = "inventory.view_band"
    template_name = "inventory/band_detail.html"
    queryset = Band.objects.prefetch_related("allowed_polarizations")


# ---------------------------------------------------------------------------
# Gateways and Hubs
# ---------------------------------------------------------------------------
class GatewayListView(InventoryListView):
    entity = "gateway"
    permission_required = "inventory.view_gateway"
    template_name = "inventory/gateway_list.html"

    def get_queryset(self) -> QuerySet[Gateway]:
        # Scope-filtered: a non-admin sees only the sites they are granted.
        queryset: QuerySet[Gateway] = Gateway.objects.for_user(self.request.user)
        return queryset.annotate(hub_count=Count("hubs")).order_by("code")


class GatewayDetailView(InventoryDetailView):
    entity = "gateway"
    permission_required = "inventory.view_gateway"
    template_name = "inventory/gateway_detail.html"

    def get_queryset(self) -> QuerySet[Gateway]:
        return Gateway.objects.for_user(self.request.user).prefetch_related("hubs")


class HubListView(InventoryListView):
    entity = "hub"
    permission_required = "inventory.view_hub"
    template_name = "inventory/hub_list.html"

    def get_queryset(self) -> QuerySet[Hub]:
        return Hub.objects.for_user(self.request.user).select_related("gateway")


class HubDetailView(InventoryDetailView):
    entity = "hub"
    permission_required = "inventory.view_hub"
    template_name = "inventory/hub_detail.html"

    def get_queryset(self) -> QuerySet[Hub]:
        return Hub.objects.for_user(self.request.user).select_related("gateway")


# ---------------------------------------------------------------------------
# Equipment Profiles
# ---------------------------------------------------------------------------
class EquipmentListView(InventoryListView):
    entity = "equipment"
    permission_required = "inventory.view_equipmentprofile"
    template_name = "inventory/equipment_list.html"

    def get_queryset(self) -> QuerySet[EquipmentProfile]:
        return EquipmentProfile.objects.select_related("band", "gateway", "hub")


class EquipmentDetailView(InventoryDetailView):
    entity = "equipment"
    permission_required = "inventory.view_equipmentprofile"
    template_name = "inventory/equipment_detail.html"
    queryset = EquipmentProfile.objects.select_related("band", "gateway", "hub")


# ---------------------------------------------------------------------------
# Guard Policies
# ---------------------------------------------------------------------------
class GuardPolicyListView(InventoryListView):
    entity = "guard-policy"
    permission_required = "inventory.view_guardpolicy"
    template_name = "inventory/guard_policy_list.html"

    def get_queryset(self) -> QuerySet[GuardPolicy]:
        return GuardPolicy.objects.annotate(window_count=Count("frequency_windows")).order_by(
            "code"
        )


class GuardPolicyDetailView(InventoryDetailView):
    entity = "guard-policy"
    permission_required = "inventory.view_guardpolicy"
    template_name = "inventory/guard_policy_detail.html"
    queryset = GuardPolicy.objects.all()


# ---------------------------------------------------------------------------
# Frequency Windows
# ---------------------------------------------------------------------------
class FrequencyWindowListView(InventoryListView):
    entity = "frequency-window"
    permission_required = "inventory.view_frequencywindow"
    template_name = "inventory/frequency_window_list.html"

    def get_queryset(self) -> QuerySet[FrequencyWindow]:
        """Current versions by default; every version when asked.

        Listing every version by default would show the same logical window three times
        with three different frequency ranges, which is the confusion versioning is meant
        to remove.
        """
        queryset = FrequencyWindow.objects.select_related(
            "satellite", "band", "default_guard_policy"
        )
        if self.request.GET.get("versions") != "all":
            queryset = queryset.filter(effective_until__isnull=True)
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["showing_all_versions"] = self.request.GET.get("versions") == "all"
        return context


class FrequencyWindowDetailView(InventoryDetailView):
    entity = "frequency-window"
    permission_required = "inventory.view_frequencywindow"
    template_name = "inventory/frequency_window_detail.html"
    queryset = FrequencyWindow.objects.select_related("satellite", "band", "default_guard_policy")


# ---------------------------------------------------------------------------
# Payload Paths
# ---------------------------------------------------------------------------
class PayloadPathListView(InventoryListView):
    entity = "payload-path"
    permission_required = "inventory.view_payloadpath"
    template_name = "inventory/payload_path_list.html"

    def get_queryset(self) -> QuerySet[PayloadPath]:
        queryset = PayloadPath.objects.select_related(
            "satellite", "uplink_window", "downlink_window"
        )
        if self.request.GET.get("versions") != "all":
            queryset = queryset.filter(effective_until__isnull=True)
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["showing_all_versions"] = self.request.GET.get("versions") == "all"
        return context


class PayloadPathDetailView(InventoryDetailView):
    entity = "payload-path"
    permission_required = "inventory.view_payloadpath"
    template_name = "inventory/payload_path_detail.html"
    queryset = PayloadPath.objects.select_related(
        "satellite", "uplink_window", "downlink_window"
    ).prefetch_related("polarization_mappings")


# ---------------------------------------------------------------------------
# Create / edit / activate — one implementation for every entity
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
    "guard-policy": EntityConfig(
        GuardPolicy, GuardPolicyForm, "inventory:guard-policy-list", "Guard Policy"
    ),
    "frequency-window": EntityConfig(
        FrequencyWindow,
        FrequencyWindowForm,
        "inventory:frequency-window-list",
        "Frequency Window",
    ),
    "payload-path": EntityConfig(
        PayloadPath, PayloadPathForm, "inventory:payload-path-list", "Payload Path"
    ),
}


def stored_copy[ModelT: models.Model](instance: ModelT) -> ModelT:
    """The row as the database currently holds it.

    ``ModelForm._post_clean`` writes the submitted values onto the instance it was handed,
    so a validated bound form leaves that object describing the *proposed* row rather than
    the stored one. Two things downstream compare a submission against what is stored —
    the audit ``before`` snapshot, and the section 13.6 guard that refuses an engineering
    change to a version already in operational use — and both would otherwise be comparing
    the new values against themselves and finding nothing changed.

    A separate object rather than ``refresh_from_db()`` so the form keeps its own instance
    for re-rendering.
    """
    # _default_manager rather than .objects: reached generically, and this is the
    # documented way to get a concrete model's manager without naming it.
    manager = cast(Any, type(instance))._default_manager
    return cast(ModelT, manager.get(pk=instance.pk))


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
                    instance=stored_copy(instance),
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
        except versioning.RetroactiveEditError as exc:
            # Section 13.6. Refusing without naming the supported route would leave the
            # user with a dead end and a strong incentive to reach for the database.
            assert instance is not None
            form.add_error(None, str(exc))
            return self._render(request, entity, config.label, form, instance, status=409)

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


class VersionHistoryView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Every version of one master-data record, oldest first.

    Read-only, and available to anyone who may view the entity: knowing *which* definition
    an allocation was validated against is part of reading the allocation, not an
    administrative extra.
    """

    permission_required = "inventory.view_satellite"

    def get(self, request: HttpRequest, entity: str, pk: str) -> HttpResponse:
        config = InventoryEditView._config(entity)
        instance = get_object_or_404(config.model, pk=pk)
        if not isinstance(instance, MasterDataVersioned):
            raise Http404(f"{config.label} records are not versioned.")

        return render(
            request,
            "inventory/version_history.html",
            {
                "entity": entity,
                "entity_label": config.label,
                "list_url": config.list_url,
                "object": instance,
                "versions": versioning.version_history(instance),
                "current": versioning.current_version(instance),
                "engineering_fields": sorted(versioning.engineering_fields_for(instance)),
                "can_manage": request.user.has_perm(MANAGE_INVENTORY),
            },
        )


class SupersedeView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """Create the next version of a master-data record. Specification section 13.6.

    The form is the entity's ordinary edit form, prefilled from the current version, with
    ``effective_from`` reinterpreted as the changeover instant: the moment the predecessor
    stops applying and the successor starts.
    """

    permission_required = MANAGE_INVENTORY

    def get(self, request: HttpRequest, entity: str, pk: str) -> HttpResponse:
        config, instance = self._resolve(entity, pk)
        form = config.form_class(instance=instance)
        # Blanked deliberately. Carrying the predecessor's start date over would be
        # rejected by the service — a successor cannot begin before the version it
        # replaces — and prefilling a value that cannot be submitted is a trap.
        form.initial["effective_from"] = None
        form.initial["effective_until"] = None
        return self._render(request, entity, config, instance, form)

    def post(self, request: HttpRequest, entity: str, pk: str) -> HttpResponse:
        config, instance = self._resolve(entity, pk)
        form = config.form_class(request.POST, instance=instance)

        if not form.is_valid():
            return self._render(request, entity, config, instance, form, status=400)

        values = form.model_values()
        # The service owns both period endpoints: it closes the predecessor at the
        # changeover and opens the successor there, leaving it open-ended.
        effective_from = values.pop("effective_from", None)
        values.pop("effective_until", None)

        try:
            successor = versioning.supersede(
                actor=request.user,
                # The predecessor as stored, not as the form has just rewritten it — see
                # stored_copy. Without this the changeover would be compared against
                # itself and every supersede would be refused as overlapping.
                instance=stored_copy(instance),
                values=values,
                effective_from=effective_from,
                reason=form.cleaned_data.get("reason", ""),
            )
        except ValueError as exc:
            form.add_error("effective_from", str(exc))
            return self._render(request, entity, config, instance, form, status=400)

        messages.success(
            request,
            f"{config.label} {successor.code} is now at version {successor.version_number}, "
            f"effective {successor.effective_from:%Y-%m-%d %H:%M}.",
        )
        return redirect(successor.get_absolute_url())

    @staticmethod
    def _resolve(entity: str, pk: str) -> tuple[EntityConfig, Any]:
        config = InventoryEditView._config(entity)
        instance = get_object_or_404(config.model, pk=pk)
        if not isinstance(instance, MasterDataVersioned):
            raise Http404(f"{config.label} records are not versioned.")
        return config, instance

    def _render(
        self,
        request: HttpRequest,
        entity: str,
        config: EntityConfig,
        instance: Any,
        form: Any,
        status: int = 200,
    ) -> HttpResponse:
        return render(
            request,
            "inventory/supersede.html",
            {
                "entity": entity,
                "entity_label": config.label,
                "list_url": config.list_url,
                "object": instance,
                "form": form,
                "next_version": instance.version_number + 1,
            },
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
