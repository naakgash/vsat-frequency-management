"""Re-apply the capability matrix after S14 added the export.

One capability, held by every role. §17.2 narrows an export by *scope*, not by capability: an
Observer exporting "all Satnet Paths" receives the same queryset the screen would have shown
them, which is the whole reason the export reuses the table's selector.

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
        ("accounts", "0013_reseed_saved_view_capability"),
        ("imports_exports", "0001_export_capability"),
    ]

    operations = [migrations.RunPython(reseed, noop)]
