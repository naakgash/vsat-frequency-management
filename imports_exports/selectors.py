"""Reading import batches. §17.1.

**No scope filtering, and that is not an oversight.** Import is administrator-only at the
capability layer (`docs/design/03` §2.1), and an administrator holds every scope. Adding a
scope filter here would be a check that can never fail, which is worse than no check: it reads
like protection and provides none.
"""

from __future__ import annotations

import dataclasses

from django.db.models import QuerySet

from imports_exports.constants import RowClassification
from imports_exports.models import ImportBatch, ImportRow


@dataclasses.dataclass(frozen=True)
class Count:
    """One classification and how many rows in this batch are it."""

    classification: str
    label: str
    total: int

    @property
    def blocking(self) -> bool:
        return self.classification in {
            RowClassification.ERROR,
            RowClassification.NEEDS_MAPPING,
            RowClassification.DUPLICATE,
        }

    @property
    def committable(self) -> bool:
        from imports_exports.constants import COMMITTABLE

        return self.classification in COMMITTABLE


def batches(limit: int = 100) -> QuerySet[ImportBatch]:
    return ImportBatch.objects.select_related("uploaded_by", "committed_by")[:limit]


def rows_of(batch: ImportBatch, *, classification: str = "") -> QuerySet[ImportRow]:
    """A batch's rows, optionally narrowed to one classification.

    An unrecognised classification is ignored rather than refused: it arrives from a query
    string, and the worst outcome of a typo should be seeing everything.
    """
    queryset = batch.rows.all()
    if classification in RowClassification.values:
        return queryset.filter(classification=classification)
    return queryset


def summary(batch: ImportBatch) -> list[Count]:
    """The seven numbers, always all seven and always in the declared order.

    Zeroes included on purpose. "No conflicts" is a thing the reviewer needs to be told, and a
    screen that simply omits the row leaves them to notice an absence.
    """
    return [
        Count(classification=value, label=label, total=batch.count_of(value))
        for value, label in RowClassification.choices
    ]


def unresolved_labels(batch: ImportBatch) -> list[dict[str, str]]:
    """Every label the batch could not place, once each.

    Once each because a spreadsheet that names the same unknown Satnet on eighty rows is one
    question, and asking it eighty times is how a review screen becomes something people click
    past.
    """
    from imports_exports.importer import fields as field_registry

    found: dict[tuple[str, str], dict[str, str]] = {}
    kinds = {field.key: field.reference for field in field_registry.INPUT_FIELDS if field.reference}

    for row in batch.rows.filter(classification=RowClassification.NEEDS_MAPPING):
        for message in row.messages:
            field = message.get("field", "")
            kind = kinds.get(field, "")
            label = str(row.normalized.get(field, "") or "")
            if not kind or not label:
                continue
            found.setdefault((kind, label), {"kind": kind, "label": label, "field": field})
    return sorted(found.values(), key=lambda entry: (entry["kind"], entry["label"]))
