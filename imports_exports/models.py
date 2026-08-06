"""No table — a place to hang the export capability. §17.2.

Django permissions belong to a model, and exporting is an action rather than a record: nothing
about a completed export is stored except the audit event (§18). ``managed = False`` gives the
permission somewhere to live without creating a table nobody would ever write to.

The import batch tables `docs/design/02` §8 describes (`import_batch`, `import_row`,
`import_mapping`) arrive with S15, which is when there is something to put in them.
"""

from __future__ import annotations

from django.db import models


class ExportPolicy(models.Model):
    """Not a record. The anchor for ``imports_exports.export_data``."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [("export_data", "Can export data")]
        verbose_name_plural = "Export policy"

    def __str__(self) -> str:
        # Unreachable in practice — there is no table and nothing constructs one — but the
        # linter is right that a model without this prints as "ExportPolicy object (None)"
        # anywhere Django renders it, and "anywhere" includes an error page.
        return "Export policy (no records)"
