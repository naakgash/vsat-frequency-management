"""Role and capability seeding.

Shared by the migrations that establish and re-establish role membership. Kept out of the
migration files themselves so each slice that introduces capabilities adds a three-line
migration rather than another copy of this logic.

Capability codenames are referenced as **strings** in
:data:`accounts.constants.CAPABILITY_MATRIX`. That is deliberate: it lets the matrix name
capabilities belonging to modules above ``accounts`` in the dependency graph without
importing them, which is what keeps the ``accounts does not import domain modules``
contract intact.
"""

from __future__ import annotations

from typing import Any

from accounts.constants import CAPABILITY_MATRIX, Role


def ensure_permissions_exist() -> None:
    """Materialise model permissions before a data migration reads them.

    Django creates ``Permission`` rows from a ``post_migrate`` signal, which fires only
    after the entire migrate run finishes. A data migration that looks up a permission
    defined by a model in the same run therefore finds nothing — on a fresh database it
    fails outright, while on an existing one it appears to work, which is the more
    dangerous of the two. Calling ``create_permissions`` explicitly closes that gap and
    is idempotent.
    """
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    for app_config in global_apps.get_app_configs():
        had_models_module = app_config.models_module
        # create_permissions() short-circuits unless models_module is set, and on a fresh
        # database during migrate it has not been populated yet. Any truthy value will do;
        # the attribute is typed as a module, hence the narrow ignore.
        app_config.models_module = True  # type: ignore[assignment]
        try:
            create_permissions(app_config, verbosity=0)
        finally:
            app_config.models_module = had_models_module


def apply_capability_matrix(apps: Any) -> None:
    """Set every role group's permissions to exactly the declared matrix.

    Authoritative rather than additive: a capability removed from the matrix is revoked,
    so the matrix in source control is the whole truth about what a role can do. That
    property is what makes ``tests/permissions/test_matrix.py`` meaningful.
    """
    ensure_permissions_exist()

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in Role.values}

    for group_name, group in groups.items():
        permissions = []
        for capability, role_names in CAPABILITY_MATRIX.items():
            if group_name not in role_names:
                continue
            app_label, codename = capability.split(".", 1)
            try:
                permissions.append(
                    Permission.objects.get(content_type__app_label=app_label, codename=codename)
                )
            except Permission.DoesNotExist as exc:  # pragma: no cover - fails the migration
                raise RuntimeError(
                    f"Capability {capability!r} is in CAPABILITY_MATRIX but no such "
                    f"permission exists. Add it to the model's Meta.permissions, or "
                    f"remove it from the matrix."
                ) from exc
        group.permissions.set(permissions)


def remove_role_groups(apps: Any) -> None:
    """Reverse of :func:`apply_capability_matrix` for the initial migration."""
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=Role.values).delete()
