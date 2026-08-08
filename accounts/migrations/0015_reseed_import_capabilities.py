"""Re-apply the capability matrix after S15 added the two import capabilities.

Both go to the administrator and to nobody else (`docs/design/03` §2.1). They are two rather
than one because they are two decisions: reading what a file would do changes nothing, while
committing it writes allocations across every Beam and Hub the file names — the widest single
write in the product, and one that object scope cannot narrow, because the file chooses what it
touches.

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
        ("accounts", "0014_reseed_export_capability"),
        ("imports_exports", "0002_import_batches"),
    ]

    operations = [migrations.RunPython(reseed, noop)]
