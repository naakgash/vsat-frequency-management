"""Running an export. §17.2, §18, §26.17.

Thin, because the workbook module does the work and `reporting.selectors` does the reading.
What lives here is the pair of things an export needs that neither of those should own: the
capability check, and the audit event.

**Every export is audited.** §18's trail answers "who saw what, and when", and an export is the
one action in the product that puts operational data somewhere the platform can no longer see.
The event records the filters and the row count rather than the rows: the trail is a record of
what happened, not a second copy of the data.
"""

from __future__ import annotations

import dataclasses

from django.utils import timezone

from accounts import policy
from accounts.types import Actor
from audit import services as audit_services
from imports_exports.constants import EXPORT_DATA, EXPORT_RUN
from imports_exports.export import normalized


@dataclasses.dataclass(frozen=True)
class Export:
    """A produced file, ready to be handed to a browser."""

    content: bytes
    filename: str
    row_count: int

    #: The one MIME type Excel and LibreOffice both accept for `.xlsx`.
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_satnet_paths(
    *,
    actor: Actor,
    filters: dict[str, str] | None = None,
    columns: list[str] | None = None,
    sort: str = "",
    reason: str = "",
) -> Export:
    """The normalized Satnet Path export, scope-filtered and recorded."""
    policy.require(actor, EXPORT_DATA, reason=reason)

    content, row_count = normalized.build(actor=actor, filters=filters, columns=columns, sort=sort)
    filename = f"satnet-paths-{timezone.now():%Y%m%d-%H%M%S}Z.xlsx"

    audit_services.record(
        action=EXPORT_RUN,
        actor=actor,
        after={
            "source": "satnet_paths.normalized",
            "filters": filters or {},
            "columns": columns or [],
            "rows": row_count,
            "filename": filename,
        },
        change_reason=reason,
        message=f"Exported {row_count} Satnet Path rows",
    )
    return Export(content=content, filename=filename, row_count=row_count)
