"""Writing the reviewed rows. §17.1, `docs/design/04` §8.4.

**Through the same service the wizard uses.** Every row goes through
``satnet_paths.services.create``, so an imported allocation gets identical calculation,
validation, containment checking, reservation and audit behaviour to one somebody typed. An
importer with its own write path is an importer that produces records the product cannot
otherwise produce, and those are the rows nobody can explain two years later.

**Everything lands as a draft.** §17.1 asks for imported conflicts to be *reported and not
activated*, and the honest reading of that is broader than conflicts: an import is bulk data
entry, not an approval. A `DRAFT` reserves no spectrum (§15.2), so a batch carrying an overlap
the incumbent spreadsheet has been living with is carried across, visible, and holding nothing —
which is the outcome that lets somebody decide what to do about it, rather than the outcome
where the migration silently drops the rows that were interesting.

**The batch policy chooses the transaction boundary**, and nothing else:

============================ ================================================================
``ALL_OR_NOTHING``           One transaction. Anything that stops a row stops the batch.
``ROW_BY_ROW``               A savepoint per row. A row that fails is recorded and the rest go.
============================ ================================================================
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction

from accounts.types import Actor
from imports_exports.constants import BatchPolicy, RowClassification
from imports_exports.importer.normalize import Message
from imports_exports.models import ImportRow
from satnet_paths import services as path_services
from satnet_paths.constants import PathStatus


class BatchRefused(Exception):
    """An all-or-nothing batch met a row it could not write, so none of it was written."""

    def __init__(self, message: str, *, row: ImportRow | None = None) -> None:
        self.row = row
        super().__init__(message)


@dataclasses.dataclass
class Outcome:
    """What a commit did."""

    created: int = 0
    skipped: int = 0
    failed: int = 0
    messages: list[str] = dataclasses.field(default_factory=list)


def write(
    *,
    actor: Actor,
    rows: list[ImportRow],
    judgements: dict[Any, Any],
    policy: str,
    reason: str = "",
) -> Outcome:
    """Write every committable row under the given policy.

    ``judgements`` carries the resolved references from the dry run's classification, keyed by
    row id, so a commit does not repeat the lookups — and, more importantly, so it resolves the
    same labels to the same records the reviewer was shown.
    """
    outcome = Outcome()

    if policy == BatchPolicy.ALL_OR_NOTHING:
        try:
            with transaction.atomic():
                for row in rows:
                    _one(
                        actor=actor,
                        row=row,
                        judgements=judgements,
                        outcome=outcome,
                        reason=reason,
                        strict=True,
                    )
        except DatabaseError as exc:
            # A constraint the services did not anticipate. The whole batch is already rolled
            # back; this turns a 500 into the sentence the reviewer needs, which is the same
            # outcome every other refusal in this batch would have produced.
            raise BatchRefused(
                f"The batch could not be written: {exc}. Nothing in it was committed — the "
                f"batch policy is all or nothing."
            ) from exc
        return outcome

    for row in rows:
        # A savepoint per row. The failure of one leaves the rows before it committed, which is
        # the entire difference between the two policies (`docs/design/04` §8.4).
        try:
            with transaction.atomic():
                _one(
                    actor=actor,
                    row=row,
                    judgements=judgements,
                    outcome=outcome,
                    reason=reason,
                    strict=False,
                )
        except DatabaseError as exc:
            # Raised out of the savepoint by a constraint the services did not anticipate. The
            # row's own record is written after the rollback, below, so it survives.
            outcome.failed += 1
            _record_failure(row, exc)
    return outcome


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _one(
    *,
    actor: Actor,
    row: ImportRow,
    judgements: dict[Any, Any],
    outcome: Outcome,
    reason: str,
    strict: bool,
) -> None:
    judgement = judgements.get(row.id)
    if judgement is None or not judgement.committable:
        outcome.skipped += 1
        return

    satnet = judgement.resolved.get("satnet")
    if satnet is None:
        outcome.skipped += 1
        return

    try:
        path = path_services.create(
            actor=actor,
            satnet=satnet,
            values=_values_for(row, judgement),
            reason=reason or f"Imported from {row.batch.file_name}",
            # §17.1's stable identifiers. A row that came from an export keeps the id it went
            # out with, so an export and a re-import are the same allocation rather than two.
            path_id=_identifier(row, "id"),
            revision_group=_identifier(row, "revision_group"),
        )
    except (path_services.PathBlockedError, ValidationError) as exc:
        # Both are refusals the services make *before* touching the database, so the surrounding
        # transaction is still usable and the row's own failure record can be written inside it.
        # A `DatabaseError` is the other kind and is caught a level up, after the rollback —
        # nothing may be written on a broken connection.
        if strict:
            raise BatchRefused(
                f"Row {row.row_number} could not be written: {exc}. Nothing in this batch was "
                f"committed — the batch policy is all or nothing.",
                row=row,
            ) from exc
        outcome.failed += 1
        _record_failure(row, exc)
        return

    row.resulting_object_id = path.pk
    row.save(update_fields=["resulting_object_id"])
    outcome.created += 1


def _values_for(row: ImportRow, judgement: Any) -> dict[str, Any]:
    """The service's argument dictionary, built from what the dry run understood.

    Read back out of `import_row.normalized` rather than re-read from the file. That is the
    second half of §17.1's guarantee: the SHA-256 proves the file has not changed, and this
    proves that what commits is what was displayed on the review screen, not a second reading
    of it.
    """
    stored = row.normalized
    values: dict[str, Any] = {
        "code": stored["code"],
        "direction": stored["direction"],
        "input_mode": stored["input_mode"],
        "input_value": int(stored["input_value"]),
        "rolloff": Decimal(str(stored["rolloff"])),
        "canonical_center_hz": int(stored["canonical_center_hz"]),
        "valid_from": datetime.datetime.fromisoformat(stored["valid_from"]),
        "status": PathStatus.DRAFT,
    }
    if stored.get("valid_until"):
        values["valid_until"] = datetime.datetime.fromisoformat(stored["valid_until"])
    if judgement.resolved.get("gateway") is not None:
        values["gateway"] = judgement.resolved["gateway"]
    return values


def _identifier(row: ImportRow, key: str) -> uuid.UUID | None:
    """A stored identifier, re-parsed rather than trusted.

    `normalized` is JSONB, so what comes back is a string that was a UUID when it went in. It is
    parsed again here because the column is writable by a migration and by anything else that
    can reach the database, and this value becomes a primary key.
    """
    value = row.normalized.get(key)
    return uuid.UUID(str(value)) if value else None


def _record_failure(row: ImportRow, exc: Exception) -> None:
    """Turn a write failure into something the review screen can show.

    Written outside the row's own transaction, which is why it is a fresh update rather than a
    change to the in-memory object: the savepoint that failed took the object's other changes
    with it.
    """
    message = Message("WRITE_FAILED", str(exc)).as_dict()
    ImportRow.objects.filter(pk=row.pk).update(
        classification=RowClassification.ERROR,
        messages=[*row.messages, message],
    )
