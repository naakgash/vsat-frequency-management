"""Re-apply the capability matrix after S12 added the lifecycle transitions.

Eight new capabilities, and the split between them is the point (§12): planning and submitting
belong to the Operator, deciding and retiring to the Approver — including for an administrator,
who is given the Approver role rather than inheriting the authority invisibly.

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
        ("accounts", "0011_reseed_satnet_path_capabilities"),
        ("satnet_paths", "0004_lifecycle_capabilities"),
        ("approvals", "0001_initial"),
    ]

    operations = [migrations.RunPython(reseed, noop)]
