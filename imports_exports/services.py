"""Running an export, and running an import. §17.1, §17.2, §18, §26.17.

The export half is thin, because the workbook module does the work and `reporting.selectors`
does the reading. The import half is the two stages of §17.1 and the seam between them:

**A dry run reads and judges, and writes nothing operational.** It stores the batch and its rows
— which are a record of what a file said, not allocations — and it calls
``satnet_paths.services.preview``, which by construction proposes and never saves (§9.3).

**A commit verifies the file again, then writes from the reviewed rows.** The SHA-256 check is
§17.1's, and it answers a question the batch id cannot: *is the file in front of you still the
file you reviewed?* An identifier proves which batch; only the digest proves that the spreadsheet
has not been edited since the numbers on the review screen were produced. So the commit asks for
the file back, hashes it, and refuses on any difference.

Having verified it, the commit writes from `import_row.normalized` rather than reading the file
a second time. That is the other half of the same guarantee: what commits is what was displayed,
not a fresh interpretation of bytes that merely hash the same.

**Every import action is audited** (§18), including a refused one. A commit that was turned away
because the file had changed is exactly the event somebody will want to find later.
"""

from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal
from typing import Any, NoReturn

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from accounts import policy
from accounts.models import User
from accounts.types import Actor
from audit import services as audit_services
from imports_exports.constants import (
    COMMIT_IMPORT,
    EXPORT_DATA,
    EXPORT_RUN,
    IMPORT_COMMITTED,
    IMPORT_DRY_RUN,
    IMPORT_REFUSED,
    RUN_IMPORT_DRYRUN,
    BatchPolicy,
    ImportStage,
    RowClassification,
)
from imports_exports.export import normalized
from imports_exports.export import workbook as writer
from imports_exports.importer import classify, commit, normalize, parse
from imports_exports.importer import fields as field_registry
from imports_exports.models import ImportBatch, ImportRow


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


# ---------------------------------------------------------------------------
# Import — stage one
# ---------------------------------------------------------------------------
class CommitRefused(Exception):
    """The commit did not happen, and the message says why."""


def dry_run(
    *,
    actor: Actor,
    content: bytes,
    file_name: str,
    batch_policy: str = BatchPolicy.ALL_OR_NOTHING,
    reason: str = "",
) -> ImportBatch:
    """Read a file, classify every row, and store what it would do. §17.1, ADR-0015."""
    policy.require(actor, RUN_IMPORT_DRYRUN, reason=reason)

    parsed = parse.read(content)
    sheets = _data_sheets(parsed)

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            file_name=file_name[:255],
            file_sha256=parsed.content_sha256,
            file_size=parsed.size,
            stage=ImportStage.DRY_RUN,
            batch_policy=batch_policy,
            uploaded_by=_acting_user(actor),
            change_reason=reason,
        )
        rows, counts = _classify_sheets(batch, sheets)
        ImportRow.objects.bulk_create(rows)

        batch.row_count = len(rows)
        batch.counts = counts
        batch.message = _summary(counts)
        batch.save(update_fields=["row_count", "counts", "message"])

    audit_services.record(
        action=IMPORT_DRY_RUN,
        actor=actor,
        obj=batch,
        after={
            "file": batch.file_name,
            "sha256": batch.file_sha256,
            "policy": batch.batch_policy,
            "rows": batch.row_count,
            "counts": batch.counts,
        },
        change_reason=reason,
        message=f"Dry run of {batch.file_name}: {batch.message}",
        import_batch_id=batch.pk,
    )
    return batch


# ---------------------------------------------------------------------------
# Import — stage two
# ---------------------------------------------------------------------------
def commit_batch(
    *,
    actor: Actor,
    batch: ImportBatch,
    content: bytes,
    reason: str = "",
) -> ImportBatch:
    """Write a reviewed batch, after proving the file is the one that was reviewed. §17.1."""
    policy.require(actor, COMMIT_IMPORT, reason=reason)

    if batch.is_committed:
        # Idempotent by refusal rather than by rewriting. Committing twice is either a double
        # click or a misunderstanding, and doing nothing while saying so is right for both.
        raise CommitRefused(
            f"{batch.file_name} was already committed on "
            f"{batch.committed_at:%Y-%m-%d %H:%M:%S} UTC. Committing it again would create "
            f"every allocation in it a second time."
        )
    if batch.stage == ImportStage.FAILED:
        raise CommitRefused(
            f"{batch.file_name} failed and cannot be committed. Upload it again once the rows "
            f"it reported have been corrected."
        )

    offered = parse.digest(content)
    if offered != batch.file_sha256:
        _refuse(
            actor,
            batch,
            reason,
            detail=(
                f"The file offered does not match the one that was reviewed "
                f"(SHA-256 {offered[:12]}… against {batch.file_sha256[:12]}…). Run the dry run "
                f"again on the file you mean to commit."
            ),
        )

    if batch.batch_policy == BatchPolicy.ALL_OR_NOTHING and batch.blocking_count:
        _refuse(
            actor,
            batch,
            reason,
            detail=(
                f"{batch.blocking_count} row(s) cannot be written and this batch is all or "
                f"nothing. Correct them and upload the file again, or re-run the dry run with "
                f"the row-by-row policy."
            ),
        )

    rows = list(batch.rows.select_related("batch").order_by("sheet", "row_number"))
    judgements = _rejudge(rows)

    try:
        outcome = commit.write(
            actor=actor,
            rows=rows,
            judgements=judgements,
            policy=batch.batch_policy,
            reason=reason,
        )
    except commit.BatchRefused as exc:
        _refuse(actor, batch, reason, detail=str(exc))

    batch.stage = ImportStage.COMMITTED
    batch.committed_by = _acting_user(actor)
    batch.committed_at = timezone.now()
    batch.counts = _counts_of(batch)
    batch.message = (
        f"{outcome.created} created, {outcome.skipped} not committable, {outcome.failed} failed"
    )
    batch.save(update_fields=["stage", "committed_by", "committed_at", "counts", "message"])

    audit_services.record(
        action=IMPORT_COMMITTED,
        actor=actor,
        obj=batch,
        after={
            "file": batch.file_name,
            "sha256": batch.file_sha256,
            "policy": batch.batch_policy,
            "created": outcome.created,
            "skipped": outcome.skipped,
            "failed": outcome.failed,
        },
        change_reason=reason,
        message=f"Committed {batch.file_name}: {batch.message}",
        import_batch_id=batch.pk,
    )
    return batch


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _data_sheets(parsed: parse.ParsedFile) -> list[parse.Sheet]:
    """The sheets that hold allocations.

    A sheet is one if it carries any heading the importer knows. The export's Data Dictionary
    and Export sheets are excluded by name as well, because both are legitimate sheets that
    happen to contain none of them and saying so by name is clearer than relying on that.
    """
    known = set(field_registry.headings())
    sheets = [
        sheet
        for sheet in parsed.sheets
        if sheet.name not in writer.AUXILIARY_SHEETS and known.intersection(sheet.headings)
    ]
    if not sheets:
        expected = ", ".join(sorted(known))
        raise parse.UnreadableFile(
            f"No sheet in this file has any column an import recognises. Expected at least one "
            f"of: {expected}. An export from this platform is the shape an import reads."
        )
    return sheets


def _classify_sheets(
    batch: ImportBatch, sheets: list[parse.Sheet]
) -> tuple[list[ImportRow], dict[str, int]]:
    seen = classify.Seen()
    rows: list[ImportRow] = []
    counts: dict[str, int] = dict.fromkeys(RowClassification.values, 0)

    for sheet in sheets:
        for number, raw in sheet.rows:
            interpreted = normalize.row(raw)
            judgement = classify.judge(raw, interpreted, seen)
            counts[judgement.classification] += 1
            _remember(seen, interpreted, judgement)
            rows.append(
                ImportRow(
                    batch=batch,
                    sheet=sheet.name,
                    row_number=number,
                    raw={key: _jsonable(value) for key, value in raw.items()},
                    normalized=interpreted.as_json(),
                    classification=judgement.classification,
                    messages=[message.as_dict() for message in judgement.messages],
                    resulting_object_id=judgement.existing_id,
                )
            )
    return rows, counts


def _remember(seen: classify.Seen, interpreted: normalize.NormalizedRow, judgement: Any) -> None:
    """Record what this row claimed, so a later row in the same file can be seen repeating it."""
    identifier = interpreted.identity.get("id")
    if identifier is not None:
        seen.identities.add(identifier)
    satnet = judgement.resolved.get("satnet")
    if satnet is not None and "code" in interpreted.values:
        seen.codes.add((str(satnet.pk), str(interpreted.values["code"])))


def _rejudge(rows: list[ImportRow]) -> dict[Any, Any]:
    """Judge the stored rows again, against the world as it is now. §9.5.

    Not a formality. A dry run reviewed on Monday and committed on Wednesday was classified
    against reservations that have since changed and master data that may have been superseded
    — the same reason the wizard re-checks a preview on save. Where the answer has changed, the
    stored row is updated, so `import_row` ends up describing what actually happened rather than
    what was expected to.

    Rows are judged in file order and each is remembered as it goes, so a file repeating an
    allocation still reports the second one as a duplicate.
    """
    seen = classify.Seen()
    judgements: dict[Any, Any] = {}

    for row in rows:
        interpreted = normalize.row(row.raw)
        judgement = classify.judge(row.raw, interpreted, seen)
        _remember(seen, interpreted, judgement)
        judgements[row.id] = judgement

        if judgement.classification != row.classification:
            row.classification = judgement.classification
            row.messages = [
                *row.messages,
                normalize.Message(
                    "RECLASSIFIED",
                    f"On commit this row was judged {judgement.classification}, not what the "
                    f"dry run reported. The spectrum and the master data behind it can change "
                    f"between a review and a commit, and the platform checks again rather than "
                    f"trusting the earlier answer.",
                ).as_dict(),
                *(message.as_dict() for message in judgement.messages),
            ]
            row.save(update_fields=["classification", "messages"])
    return judgements


def _counts_of(batch: ImportBatch) -> dict[str, int]:
    counts = dict.fromkeys(RowClassification.values, 0)
    for entry in batch.rows.values("classification").annotate(total=Count("id")):
        counts[entry["classification"]] = entry["total"]
    return counts


def _refuse(actor: Actor, batch: ImportBatch, reason: str, *, detail: str) -> NoReturn:
    """Record a refused commit and raise. §18.

    The batch is **not** marked failed: nothing about it has changed, and a refusal caused by
    somebody uploading last month's file must not destroy a review that is still valid.
    """
    audit_services.record(
        action=IMPORT_REFUSED,
        actor=actor,
        obj=batch,
        after={"file": batch.file_name, "detail": detail},
        change_reason=reason,
        message=f"Refused to commit {batch.file_name}: {detail}",
        import_batch_id=batch.pk,
    )
    raise CommitRefused(detail)


def _summary(counts: dict[str, int]) -> str:
    parts = [
        f"{count} {RowClassification(name).label.lower()}"
        for name, count in counts.items()
        if count
    ]
    return ", ".join(parts) or "no rows"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _acting_user(actor: Actor) -> User | None:
    return actor if isinstance(actor, User) else None
