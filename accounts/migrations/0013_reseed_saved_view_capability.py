"""Re-apply the capability matrix after S13 added saved views.

One capability, held by every role. Saving a table setup is a personal working tool rather than
an operational action — an Observer whose job is reading tables needs it most.

Permission changes reach production as a reviewed migration rather than as a silent deployment
side effect (specification section 22.3).
"""

from django.db import migrations

from accounts.seeding import apply_capability_matrix


def reseed(apps, schema_editor):
    apply_capability_matrix(apps)


def noop(apps, schema_editor):
    """No reverse: the previous migration's forward function is the reverse."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_reseed_lifecycle_capabilities"),
        ("reporting", "0001_saved_view"),
    ]

    operations = [migrations.RunPython(reseed, noop)]
