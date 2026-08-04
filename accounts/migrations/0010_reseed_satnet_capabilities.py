"""Re-apply the capability matrix after S10 added Satnets.

Permission changes reach production as a reviewed migration rather than as a silent
deployment side effect (specification section 22.3).
"""

from django.db import migrations

from accounts.seeding import apply_capability_matrix


def reseed(apps, schema_editor):
    apply_capability_matrix(apps)


def noop(apps, schema_editor):
    """No reverse: the previous migration's forward function is the reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_userbeamscope"),
        ("satnets", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(reseed, noop),
    ]
