"""Create the four role groups and assign their capabilities.

Roles are structural, not sample data, so they belong in a migration: the application is
not functional without them, and every environment needs identical role definitions.

Demo *users* are deliberately not created here — see
``accounts/management/commands/seed_demo.py``. A migration that created accounts with
known passwords would run in production too.

The assignment is driven by ``CAPABILITY_MATRIX``, and is idempotent: re-running sets
each group's permissions to exactly the matrix, so a capability removed from the matrix
is revoked rather than lingering.
"""

from django.db import migrations

from accounts.constants import CAPABILITY_MATRIX, Role


def ensure_permissions_exist(apps):
    """Materialise model permissions before this migration reads them.

    Django creates ``Permission`` rows from a ``post_migrate`` signal, which fires only
    after the entire migrate run finishes. A data migration that looks up a permission
    defined by a model in the same run therefore finds nothing — on a fresh database it
    would fail, while on an existing one it would appear to work, which is the more
    dangerous of the two outcomes.

    Calling ``create_permissions`` explicitly closes that gap. It is idempotent.
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        # create_permissions() short-circuits unless models_module is set; on a fresh
        # database during migrate it has not been populated yet.
        had_models_module = app_config.models_module
        app_config.models_module = True
        try:
            create_permissions(app_config, verbosity=0)
        finally:
            app_config.models_module = had_models_module


def seed_roles(apps, schema_editor):
    ensure_permissions_exist(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in Role.values}

    # capability codename -> the groups that should hold it
    wanted: dict[str, set[str]] = {}
    for capability, roles in CAPABILITY_MATRIX.items():
        wanted[capability] = set(roles)

    for group_name, group in groups.items():
        permissions = []
        for capability, role_names in wanted.items():
            if group_name not in role_names:
                continue
            app_label, codename = capability.split(".", 1)
            try:
                permissions.append(
                    Permission.objects.get(
                        content_type__app_label=app_label, codename=codename
                    )
                )
            except Permission.DoesNotExist as exc:  # pragma: no cover - fails the migration
                raise RuntimeError(
                    f"Capability {capability!r} is in CAPABILITY_MATRIX but no such "
                    f"permission exists. Add it to the model's Meta.permissions, or "
                    f"remove it from the matrix."
                ) from exc
        group.permissions.set(permissions)


def unseed_roles(apps, schema_editor):
    """Remove the role groups.

    Group membership is removed with them, which is correct for a reversal: leaving
    orphaned groups behind would let a re-apply silently inherit stale permissions.
    """
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=Role.values).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("audit", "0001_initial"),
        # Permissions are created by a post-migrate signal on the content types app;
        # depending on it ensures they exist before this migration looks them up.
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed_roles),
    ]
