"""Capabilities, enumerations and audit actions for import and export."""

from __future__ import annotations

from django.db import models

#: §17.2. Held by every role, and narrowed by scope rather than by capability: an Observer
#: exporting "all Satnet Paths" receives their own scope, which is the same queryset the screen
#: would have shown them.
EXPORT_DATA = "imports_exports.export_data"

#: §17.1, and **administrator only** (`docs/design/03` §2.1). Both halves are separate
#: capabilities because they are separate decisions: reading what a file *would* do changes
#: nothing, while committing it writes allocations under somebody else's Satnets. A single
#: "import" capability would make the safe half impossible to grant on its own.
RUN_IMPORT_DRYRUN = "imports_exports.run_import_dryrun"
COMMIT_IMPORT = "imports_exports.commit_import"

EXPORT_RUN = "EXPORT_RUN"
IMPORT_DRY_RUN = "IMPORT_DRY_RUN"
IMPORT_COMMITTED = "IMPORT_COMMITTED"
IMPORT_REFUSED = "IMPORT_REFUSED"
IMPORT_MAPPING_REMEMBERED = "IMPORT_MAPPING_REMEMBERED"


class ImportStage(models.TextChoices):
    """Where a batch is in the two-stage import. §17.1, ADR-0015.

    There is no ``UPLOADED``: a file that has been read but not classified is not a state the
    product can be in, because the read *is* the classification. A batch exists from the moment
    its dry run finishes.
    """

    DRY_RUN = "DRY_RUN", "Dry run"
    COMMITTED = "COMMITTED", "Committed"
    FAILED = "FAILED", "Failed"


class BatchPolicy(models.TextChoices):
    """What a failing row does to the rest of the batch. `docs/design/04` §8.4."""

    ALL_OR_NOTHING = "ALL_OR_NOTHING", "All or nothing"
    ROW_BY_ROW = "ROW_BY_ROW", "Row by row"


class RowClassification(models.TextChoices):
    """The seven outcomes a row can have. §17.1, `docs/design/02` §8.

    Ordered from least to most committable, which is also the order
    :mod:`imports_exports.importer.classify` tests them in: a row that is several of these at
    once is reported as the most blocking one, because telling somebody their row needs a
    mapping when it is also a free-capacity row that should never have been read would send
    them to fix the wrong thing.
    """

    IGNORED_FREE_CAPACITY = "IGNORED_FREE_CAPACITY", "Ignored — free capacity"
    ERROR = "ERROR", "Error"
    NEEDS_MAPPING = "NEEDS_MAPPING", "Needs mapping"
    DUPLICATE = "DUPLICATE", "Duplicate"
    CONFLICT = "CONFLICT", "Conflict"
    WARNING = "WARNING", "Warning"
    VALID = "VALID", "Valid"


#: The classifications a commit writes a Satnet Path for. A ``CONFLICT`` row **is** committed —
#: as a draft, like every imported row — because §17.1 asks for imported conflicts to be
#: *reported and not activated* rather than discarded: the allocation the incumbent spreadsheet
#: holds is real, and refusing to carry it across would lose the very overlap the migration
#: exists to surface.
COMMITTABLE = frozenset(
    {RowClassification.VALID, RowClassification.WARNING, RowClassification.CONFLICT}
)
