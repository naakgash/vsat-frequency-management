from django.apps import AppConfig


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
        from inventory.models import Band, EquipmentProfile, Gateway, Hub

        scope_registry.register(Gateway, scope.gateway_in_scope)
        scope_registry.register(Hub, scope.hub_in_scope)

        # Dependencies within inventory itself. Modules above register their own as they
        # land: Frequency Windows and Payload Paths in S5, Beams in S8.
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
