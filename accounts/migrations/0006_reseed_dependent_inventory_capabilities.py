"""Re-apply the capability matrix after slice S5 added dependent inventory."""

from django.db import migrations

from accounts.seeding import apply_capability_matrix


def reseed(apps, schema_editor):
    apply_capability_matrix(apps)


def noop(apps, schema_editor):
    """No reverse: the previous migration's forward function is the reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_reseed_inventory_capabilities"),
        ("inventory", "0002_dependent_inventory"),
    ]

    operations = [
        migrations.RunPython(reseed, noop),
    ]
