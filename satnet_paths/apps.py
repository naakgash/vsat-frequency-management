from django.apps import AppConfig


class SatnetPathsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "satnet_paths"
    verbose_name = "Satnet Paths"

    def ready(self) -> None:
        from accounts import scope as scope_registry
        from satnet_paths import scope
        from satnet_paths.models import SatnetPath

        scope_registry.register(SatnetPath, scope.satnet_path_in_scope)
