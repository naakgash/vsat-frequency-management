"""The normalized Satnet Path export. §17.2, §26.19.

*Normalized* means the platform's own shape: one row per current Satnet Path revision, one
column per registry column, identifiers included so the file can come back through the S15
importer and be matched to what it came from. It is not the incumbent spreadsheet's layout —
that is the legacy export, and it is **OQ-18**.

**The export is the table.** Same columns, same filters, same scope-filtered queryset, because
an export that answered a slightly different question from the screen somebody was looking at is
worse than no export: they would reconcile the difference by hand and conclude the platform is
wrong.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from accounts.types import Actor
from imports_exports.export import workbook as writer
from reporting import columns as column_registry
from reporting import selectors as reporting_selectors

SHEET_NAME = "Satnet Paths"

#: Always exported, whatever columns were chosen, and always first. §17.2 wants an export to be
#: matchable against its source; the S15 importer will read this column to decide whether a row
#: is an update or a creation, and a file without it can only ever create duplicates.
IDENTITY_COLUMNS = ("id", "revision_group")


def build(
    *,
    actor: Actor,
    filters: dict[str, str] | None = None,
    columns: list[str] | None = None,
    sort: str = "",
) -> tuple[bytes, int]:
    """Produce the workbook and say how many rows it holds.

    The count comes back rather than being read off the sheet because it is what the audit
    event records, and counting rows in a file to find out what you just wrote is the kind of
    indirection that eventually disagrees with itself.
    """
    chosen = column_registry.resolve(columns)
    rows = list(reporting_selectors.table(actor, filters=filters or {}, sort=sort))

    book = writer.new_workbook()
    sheet = book.create_sheet(SHEET_NAME)
    headings = [*IDENTITY_COLUMNS, *(heading(column) for column in chosen)]
    writer.write_header(sheet, headings)

    for index, path in enumerate(rows, start=2):
        writer.write_row(
            sheet,
            index,
            [path.id, path.revision_group, *(_value(path, column) for column in chosen)],
        )
    writer.autosize(sheet, headings)

    writer.add_data_dictionary(book, [c.spec_code for c in chosen if c.spec_code])
    writer.add_provenance(
        book,
        writer.Provenance(
            exported_by=str(getattr(actor, "username", "")) or "unknown",
            exported_at=timezone.now(),
            source="Satnet Paths (normalized)",
            filters=filters or {},
            columns=[column.key for column in chosen],
            row_count=len(rows),
            notes=(
                "Rows are the current revision of each allocation, within the exporter's scope. "
                "The id and revision_group columns are what an import matches on."
            ),
        ),
    )
    return writer.to_bytes(book), len(rows)


def heading(column: column_registry.Column) -> str:
    """What the column is called in the file.

    The **code**, not the display name, for a specification column: §10.3 uses the code as the
    compact representation and the Data Dictionary sheet explains it, so a workbook whose
    headings an administrator had renamed would stop matching the importer that reads it.

    A timestamp column says "(UTC)". The xlsx format cannot carry a time zone on a cell, so
    **A-28**'s requirement that a timestamp always states its zone is met in the heading — see
    `workbook._writable` for why the alternative (an ISO string) was not taken.
    """
    name = column.spec_code or column.label
    return f"{name} (UTC)" if column.render == "utc" else name


def _value(path: Any, column: column_registry.Column) -> Any:
    """One cell's value, straight off the row.

    Deliberately *not* the rendered form. A frequency goes out as integer Hz and a timestamp as
    a datetime, because a spreadsheet that received "29,145.000" would have to parse a
    thousands separator to get back a number, and the platform's own unit is Hz (**A-08**).
    """
    return column.value_of(path)
