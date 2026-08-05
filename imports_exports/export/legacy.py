"""The legacy-style export. **OQ-18** — deliberately not built.

§17.2 asks for an export that matches the incumbent spreadsheet closely enough that the people
who use it today can keep working. That is a real requirement and it is not implementable from
the specification alone: it needs the actual workbook — its sheet names, its column order, its
merged headers, the units each column is written in, and whichever conventions have accumulated
in it over the years.

Writing one from a description would produce a file that looks approximately right and is wrong
in ways nobody notices until a migration comparison in Phase 9 shows differences that turn out
to be the export's fault rather than the engine's. So the module exists to say what is missing
and to fail loudly if something calls it, rather than shipping a plausible layout.

The normalized export in :mod:`imports_exports.export.normalized` carries the same data in the
platform's own shape and is complete.
"""

from __future__ import annotations

from typing import Any

#: What has to arrive before this can be built. Named here so the gap is findable from the code
#: as well as from the register.
REQUIREMENT = (
    "A real sample of the incumbent workbook (OQ-18): sheet names, column order and headers, "
    "the unit each column is written in, and any conventions the current users rely on."
)


class LegacyExportUnavailable(NotImplementedError):
    """Raised instead of guessing at a layout."""


def build(**_: Any) -> bytes:
    """Refuse, with the reason.

    A stub returning an empty workbook would be worse: somebody would ship it, and an empty
    file is indistinguishable from a filter that matched nothing.
    """
    raise LegacyExportUnavailable(
        f"The legacy-style export is not implemented. {REQUIREMENT} Until then the normalized "
        f"export carries the same data in the platform's own shape."
    )
