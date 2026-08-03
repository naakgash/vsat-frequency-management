"""Create the four role groups and assign their capabilities.

Roles are structural, not sample data, so they belong in a migration: the application is
not functional without them, and every environment needs identical role definitions.

Demo *users* are deliberately not created here — see
``accounts/management/commands/seed_demo.py``. A migration that created accounts with
known passwords would run in production too.

The assignment logic lives in ``accounts/seeding.py`` so that later slices which add
capabilities need only a short re-seed migration.
"""

from django.db import migrations

from accounts.seeding import apply_capability_matrix, remove_role_groups


def seed_roles(apps, schema_editor):
    apply_capability_matrix(apps)


def unseed_roles(apps, schema_editor):
    remove_role_groups(apps)


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
