"""Which column is which field, and which columns are never believed. §17.1.

**The import's vocabulary is the export's.** Every heading named here is produced by
`imports_exports.export.normalized`, which takes it from `reporting.columns`, which takes it
from the Specification Dictionary — one chain, so an export written today reads back tomorrow
and a renamed column breaks visibly in one place instead of silently in two.

Three kinds of column:

* :data:`INPUT_FIELDS` — what an operator typed. These are the only cells whose values reach a
  service. §9.2's pair (`input_mode`, `input_value`) is here because it is the one thing in the
  record that recalculation cannot recover.
* :data:`IDENTITY_FIELDS` — ``id`` and ``revision_group``, which say *which* allocation a row
  is, not what it says.
* :data:`DERIVED_HEADINGS` — bandwidths, guards, edges, IF. Read, compared, and **never used**
  (§17.1). A file that disagrees with the engine gets a warning; the engine's answer is what is
  written.

What is deliberately not imported is listed at the bottom, with the reason.
"""

from __future__ import annotations

import dataclasses

from imports_exports.export import normalized
from reporting import columns as column_registry

#: Reference kinds. The string is stored on `ImportMapping.kind`, so it is data and renaming one
#: is a migration.
SATNET = "satnet"
GATEWAY = "gateway"


@dataclasses.dataclass(frozen=True)
class Field:
    """One column an import may set.

    ``column`` is the key in `reporting.columns`; the heading is derived from it rather than
    written out, because an export whose heading changed and an importer that kept the old one
    would produce a file that cannot be read by the product that wrote it.
    """

    key: str
    column: str
    #: ``text``, ``choice``, ``integer``, ``decimal`` or ``utc`` — see
    #: :mod:`imports_exports.importer.normalize`, which is the only reader of this.
    kind: str
    required: bool = False
    #: For a reference: which registry the label is looked up in. Empty for a plain value.
    reference: str = ""
    choices: tuple[str, ...] = ()

    @property
    def heading(self) -> str:
        """The heading the export writes for this field."""
        return normalized.heading(column_registry.BY_KEY[self.column])


#: What the file says *which* allocation a row is. Present on any file the platform produced,
#: absent on one somebody typed — both are legitimate, and the difference is what separates a
#: re-import from a first import.
IDENTITY_FIELDS: tuple[str, ...] = normalized.IDENTITY_COLUMNS


INPUT_FIELDS: tuple[Field, ...] = (
    Field("code", "code", "text", required=True),
    Field("satnet", "satnet", "text", required=True, reference=SATNET),
    Field("direction", "direction", "choice", required=True, choices=("FWD", "RTN")),
    Field(
        "input_mode",
        "input_mode",
        "choice",
        required=True,
        choices=("OCCUPIED_BW", "SYMBOL_RATE"),
    ),
    Field("input_value", "input_value", "integer", required=True),
    Field("rolloff", "rolloff", "decimal", required=True),
    Field("canonical_center_hz", "canonical_center", "integer", required=True),
    Field("valid_from", "valid_from", "utc", required=True),
    Field("valid_until", "valid_until", "utc"),
    Field("gateway", "gateway", "text", reference=GATEWAY),
)

BY_KEY: dict[str, Field] = {field.key: field for field in INPUT_FIELDS}

#: Columns whose values the engine owns (§26.16). Read so they can be checked, never used.
DERIVED_COLUMNS: tuple[str, ...] = (
    "symbol_rate",
    "occupied_bw",
    "allocated_bw",
    "guard_left",
    "guard_right",
    "canonical_start",
    "canonical_end",
    "translated_center",
    "translated_start",
    "translated_end",
)


def headings() -> dict[str, str]:
    """Heading → field key, for every column an import may set."""
    return {field.heading: field.key for field in INPUT_FIELDS}


def derived_headings() -> dict[str, str]:
    """Heading → the Satnet Path attribute it claims to hold.

    Used only to compare. The mapping goes to a model attribute rather than to a service
    argument on purpose: there is no service argument, because none of these is an input.
    """
    return {
        normalized.heading(column_registry.BY_KEY[key]): column_registry.BY_KEY[key].attribute
        for key in DERIVED_COLUMNS
    }


def required_keys() -> tuple[str, ...]:
    return tuple(field.key for field in INPUT_FIELDS if field.required)


# ---------------------------------------------------------------------------
# Not imported, and why
# ---------------------------------------------------------------------------
#: A **guard policy** is an engineering override. The resolved widths are recomputed from the
#: Satnet's default through ADR-0016's hierarchy, and a column of policy codes in a spreadsheet
#: would let a bulk upload change the guards on a hundred allocations without anybody choosing
#: to. Where the file's guard widths disagree with the resolved ones, the row is warned about.
#:
#: A **decimator** is time-bounded (ADR-0021): the record points at a `DecimatorAssignment`,
#: not at the box, and picking which assignment a box name meant over a given period is a guess.
#: The export's Decimator column names the unit, which is not enough to resolve to one.
#:
#: An **equipment profile** is the same shape of problem and the IF values that follow from it
#: are derived, so a row that named one would be asking the importer to choose hardware.
NOT_IMPORTED: tuple[tuple[str, str], ...] = (
    ("guard_policy", "an engineering override; the guards are resolved through ADR-0016"),
    ("decimator_assignment", "time-bounded; a unit name does not identify an assignment"),
    ("equipment_profile", "would make the importer choose hardware"),
)
