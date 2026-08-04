from django.apps import AppConfig
from django.db.models import Q


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventory"

    def ready(self) -> None:
        """Register scope resolvers and dependency declarations.

        Both registries live in modules below inventory, which cannot import it. Pushing
        the registrations from here is what lets ``accounts`` stay free of domain imports
        while still applying inventory's scope rules.
        """
        from accounts import scope as scope_registry
        from inventory import dependencies, scope
        from inventory.models import (
            Band,
            EquipmentProfile,
            FrequencyWindow,
            Gateway,
            GuardPolicy,
            Hub,
            PayloadPath,
            Satellite,
            SpectrumResource,
        )

        scope_registry.register(Gateway, scope.gateway_in_scope)
        scope_registry.register(Hub, scope.hub_in_scope)

        # --- Dependencies within inventory -------------------------------------
        # Modules above register their own as they land: Beams in S8, Satnets in S10,
        # Satnet Paths in S11. This registry is how they do that without inventory
        # importing them.
        dependencies.register(
            Gateway,
            label="Hubs",
            count=lambda gateway: Hub.objects.filter(gateway=gateway).count(),
        )
        dependencies.register(
            Gateway,
            label="Equipment Profiles",
            count=lambda gateway: EquipmentProfile.objects.filter(gateway=gateway).count(),
        )
        dependencies.register(
            Hub,
            label="Equipment Profiles",
            count=lambda hub: EquipmentProfile.objects.filter(hub=hub).count(),
        )
        dependencies.register(
            Band,
            label="Equipment Profiles",
            count=lambda band: EquipmentProfile.objects.filter(band=band).count(),
        )
        dependencies.register(
            Band,
            label="Frequency Windows",
            count=lambda band: FrequencyWindow.objects.filter(band=band).count(),
        )
        dependencies.register(
            Satellite,
            label="Frequency Windows",
            count=lambda satellite: FrequencyWindow.objects.filter(satellite=satellite).count(),
        )
        dependencies.register(
            Satellite,
            label="Payload Paths",
            count=lambda satellite: PayloadPath.objects.filter(satellite=satellite).count(),
        )
        dependencies.register(
            Satellite,
            label="Spectrum Resources",
            count=lambda satellite: SpectrumResource.objects.filter(satellite=satellite).count(),
        )
        # A window referenced by a payload path has its engineering values frozen: an
        # allocation validated against those numbers must keep them (section 13.6).
        dependencies.register(
            FrequencyWindow,
            label="Payload Paths",
            count=lambda window: PayloadPath.objects.filter(
                Q(uplink_window=window) | Q(downlink_window=window)
            ).count(),
        )
        dependencies.register(
            GuardPolicy,
            label="Frequency Windows",
            count=lambda policy: FrequencyWindow.objects.filter(
                default_guard_policy=policy
            ).count(),
        )
