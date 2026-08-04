from django.apps import AppConfig
from django.db import models


class BeamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "beams"
    verbose_name = "Beams"

    def ready(self) -> None:
        """Register the Beam's scope resolver and its dependencies on inventory.

        Both registries live in modules below ``beams``, which cannot import it. Pushing the
        registrations from here is what lets ``accounts`` stay free of domain imports and
        ``inventory`` stay unaware of what depends on it (the same pattern as S4).
        """
        from accounts import scope as scope_registry
        from beams import scope
        from beams.models import Beam, BeamDirectionConfig
        from inventory import dependencies
        from inventory.models import EquipmentProfile, FrequencyWindow, PayloadPath, Satellite

        scope_registry.register(Beam, scope.beam_in_scope)

        # A Beam holds its master data in place: superseding a Payload Path a Beam points at
        # would change what every Satnet Path under it was validated against (ADR-0012).
        dependencies.register(
            Satellite,
            label="Beams",
            count=lambda satellite: Beam.objects.filter(satellite=satellite).count(),
        )
        dependencies.register(
            PayloadPath,
            label="Beam directions",
            count=lambda path: BeamDirectionConfig.objects.filter(payload_path=path).count(),
        )
        dependencies.register(
            FrequencyWindow,
            label="Beam directions",
            count=lambda window: BeamDirectionConfig.objects.filter(
                models.Q(uplink_window=window) | models.Q(downlink_window=window)
            ).count(),
        )
        dependencies.register(
            EquipmentProfile,
            label="Beam directions",
            count=lambda profile: profile.beam_directions.count(),
        )
