"""What an import remembers. §17.1, `docs/design/02` §8.

Three tables, and one non-table.

**A dry run is a record, not a preview that vanishes.** Every row the file contained is stored
with what it was read as, what it was classified as and why — because the review screen is
somebody deciding whether to write hundreds of allocations, and "it said something about row
214 last time" is not a basis for that decision. It is also what makes the commit honest: the
rows that commit are the rows that were reviewed, not a second reading of a file that may have
changed in between (§17.1, ADR-0015).

**Nothing here is production data.** An `import_row` is a record of what a spreadsheet said. The
allocation it produced is a Satnet Path like any other, written through the same service the
wizard uses, and `resulting_object_id` is the only link between the two.
"""

from __future__ import annotations

import uuid

from django.db import models

from imports_exports.constants import BatchPolicy, ImportStage, RowClassification


class ExportPolicy(models.Model):
    """Not a record. The anchor for ``imports_exports.export_data``.

    Django permissions belong to a model, and exporting is an action rather than a record:
    nothing about a completed export is stored except the audit event (§18). ``managed = False``
    gives the permission somewhere to live without creating a table nobody would ever write to.
    """

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


class ImportBatch(models.Model):
    """One upload, read once and possibly committed once. §17.1."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    file_name = models.CharField(max_length=255)
    #: The digest of the bytes that were read. Commit refuses unless the file offered to it
    #: hashes to the same value (§17.1) — see ``services.commit`` for why that is a re-upload
    #: rather than a stored copy.
    file_sha256 = models.CharField(max_length=64)
    file_size = models.BigIntegerField(default=0)

    stage = models.CharField(
        max_length=16, choices=ImportStage.choices, default=ImportStage.DRY_RUN
    )
    batch_policy = models.CharField(
        max_length=16, choices=BatchPolicy.choices, default=BatchPolicy.ALL_OR_NOTHING
    )

    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.PROTECT, related_name="import_batches"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    committed_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="committed_import_batches",
    )
    committed_at = models.DateTimeField(null=True, blank=True)

    row_count = models.PositiveIntegerField(default=0)
    #: Rows per classification, as a mapping. Denormalised from `import_row` on purpose: the
    #: list screen shows the seven numbers for every batch, and computing them there is one
    #: grouped query per row of the list. They are written once, by the dry run, and the rows
    #: they count are immutable afterwards — so unlike free capacity (§16, ADR-0009) this
    #: cannot go stale.
    counts = models.JSONField(default=dict, blank=True)

    change_reason = models.TextField(blank=True)
    message = models.TextField(blank=True)

    class Meta:
        db_table = "import_batch"
        ordering = ["-uploaded_at"]
        default_permissions = ("view",)
        #: Administrator only (`docs/design/03` §2.1). Two capabilities, not one: reading what
        #: a file would do is safe, and writing what it says is not.
        permissions = [
            ("run_import_dryrun", "Can upload a file and see what it would do"),
            ("commit_import", "Can commit a reviewed import batch"),
        ]
        indexes = [
            models.Index(fields=["stage", "-uploaded_at"], name="import_batch_stage_idx"),
            models.Index(fields=["file_sha256"], name="import_batch_sha_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.get_stage_display()})"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("imports:detail", kwargs={"pk": self.pk})

    @property
    def is_committed(self) -> bool:
        return self.stage == ImportStage.COMMITTED

    def count_of(self, classification: str) -> int:
        return int(self.counts.get(classification, 0))

    @property
    def committable_count(self) -> int:
        from imports_exports.constants import COMMITTABLE

        return sum(self.count_of(name) for name in COMMITTABLE)

    @property
    def blocking_count(self) -> int:
        """Rows that stop an all-or-nothing commit.

        Free-capacity rows are **not** counted: they are a correct outcome, not a failure, and
        a batch of a hundred allocations and forty gap rows must not read as forty problems.
        """
        return sum(
            self.count_of(name)
            for name in (
                RowClassification.ERROR,
                RowClassification.NEEDS_MAPPING,
                RowClassification.DUPLICATE,
            )
        )


class ImportRow(models.Model):
    """One spreadsheet row, as read and as judged. §17.1."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")

    sheet = models.CharField(max_length=100)
    #: The row number **in the file**, so a message can be acted on without counting. It is not
    #: an index into anything here: rows the reader skipped still have their own numbers, and a
    #: message about "row 7" has to mean row 7 of the spreadsheet.
    row_number = models.PositiveIntegerField()

    #: Exactly what the cells held, keyed by heading, before any interpretation. Kept because
    #: the first question about a misread row is always "what did the file actually say".
    raw = models.JSONField(default=dict, blank=True)
    #: What the row was understood to mean, in the platform's own units and vocabulary.
    normalized = models.JSONField(default=dict, blank=True)

    classification = models.CharField(max_length=24, choices=RowClassification.choices)
    #: Why. A list of ``{"code", "text", "field"}`` objects rather than one string, so the
    #: review screen can group and the audit trail can be searched by code.
    messages = models.JSONField(default=list, blank=True)

    #: The Satnet Path this row produced, once it has produced one. Null on a dry run, and null
    #: forever for a row that was never committable.
    resulting_object_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "import_row"
        ordering = ["sheet", "row_number"]
        default_permissions = ("view",)
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "sheet", "row_number"], name="uq_import_row_position"
            ),
        ]
        indexes = [
            models.Index(fields=["batch", "classification"], name="import_row_class_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sheet}!{self.row_number} — {self.classification}"


class ImportMapping(models.Model):
    """A label somebody once told the platform the meaning of. §17.1.

    The incumbent spreadsheet names a Satnet or a Gateway the way its authors write it, which is
    not always the way the platform holds it. The first import asks; every import afterwards
    does not, because being asked the same question forty times is how a reviewer starts
    clicking through the review screen without reading it.

    Deliberately **not** a fuzzy matcher. A mapping is an exact label somebody confirmed, and
    the platform's own code is tried first — a near-match that resolved automatically would
    quietly attach an allocation to the wrong Satnet, which is the one mistake an import must
    not be able to make on its own.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: Which registry the label belongs to — ``satnet``, ``gateway``. A plain string rather than
    #: a content type: the importer resolves a small, declared set of references, and a
    #: content-type foreign key would suggest it resolves any model.
    kind = models.CharField(max_length=40)
    label = models.CharField(max_length=255)

    target_id = models.UUIDField()
    #: What the target was called when the mapping was made. Stored so a mapping list is
    #: readable without joining to five different tables, and so a mapping to something later
    #: deactivated still says what it pointed at.
    target_repr = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.PROTECT, related_name="import_mappings"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "import_mapping"
        ordering = ["kind", "label"]
        default_permissions = ("view",)
        constraints = [
            models.UniqueConstraint(fields=["kind", "label"], name="uq_import_mapping_label"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.label} → {self.target_repr or self.target_id}"
