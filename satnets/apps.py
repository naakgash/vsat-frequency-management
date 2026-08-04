from django.apps import AppConfig


class SatnetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "satnets"
    verbose_name = "Satnets"

    def ready(self) -> None:
        from accounts import scope as scope_registry
        from satnets import scope
        from satnets.models import Satnet

        scope_registry.register(Satnet, scope.satnet_in_scope)
