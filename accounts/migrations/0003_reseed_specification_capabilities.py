"""Re-apply the capability matrix after slice S3 added the Specification Dictionary.

Permission changes reach production as a reviewed migration rather than as a silent
side effect of deployment — specification section 22.3 makes migration review a distinct
release step, and a change to what a role may do is exactly the kind of change that
warrants it.
"""

from django.db import migrations

from accounts.seeding import apply_capability_matrix


def reseed(apps, schema_editor):
    apply_capability_matrix(apps)


def noop(apps, schema_editor):
    """No reverse: the previous migration's forward function is the reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_seed_roles"),
        ("specifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(reseed, noop),
    ]
