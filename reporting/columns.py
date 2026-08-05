"""What a Satnet Path table may show, and where each heading comes from. §10.3, §2.

**The headings are not in the template.** §2 forbids the same description living in more than
one place, and a table is the worst offender: twenty columns, each with a name, a unit and an
explanation, all of which an administrator may edit. So a column names a **specification code**
and the heading, unit, precision and info popover are rendered from the dictionary through the
existing `spec_code` tag. A column with no code in the dictionary — a status badge, a link to
the Satnet — carries a plain label, and that is stated per column rather than left to be
inferred.

Grouped, because §10.3 asks for grouped columns and because thirty flat checkboxes is not a
column picker anybody uses. The groups follow the record's own shape: what was asked for, what
was computed, and each side of the payload.

**Every column is a value, not a callable on the model.** ``value_of`` reads attributes only.
A column that could run arbitrary code would be one query away from an N+1 on a page whose whole
purpose is to render two hundred rows.
"""

from __future__ import annotations

import dataclasses
from typing import Any

#: Groups, in the order a table shows them. The key is stored in a saved view, so renaming one
#: is a data migration rather than a cosmetic change.
GROUPS: tuple[tuple[str, str], ...] = (
    ("identity", "Identity"),
    ("lifecycle", "Lifecycle"),
    ("request", "What was asked for"),
    ("bandwidth", "Bandwidth"),
    ("canonical", "Canonical side"),
    ("translated", "Translated side"),
    ("hardware", "Hardware"),
)


@dataclasses.dataclass(frozen=True)
class Column:
    """One column of the Satnet Path table.

    ``spec_code`` is the dictionary entry that supplies the heading and its explanation.
    ``label`` is the fallback for the handful of columns that are not specification values —
    a code, a link, a status badge — and exactly one of the two is set.
    """

    key: str
    group: str
    attribute: str
    spec_code: str = ""
    label: str = ""
    #: How the cell renders: ``text``, ``mhz``, ``link``, ``status``, ``utc`` or ``number``.
    render: str = "text"
    #: The queryset expression this column sorts by, when it can be sorted at all. A column
    #: over a derived property has none, and the table says so rather than offering a control
    #: that quietly does nothing.
    order_by: str = ""
    default: bool = False

    def __post_init__(self) -> None:
        if bool(self.spec_code) == bool(self.label):
            raise ValueError(
                f"{self.key}: a column takes its heading from the dictionary (spec_code) or "
                f"carries its own (label), never both and never neither."
            )

    @property
    def sortable(self) -> bool:
        return bool(self.order_by)

    def value_of(self, path: Any) -> Any:
        """Read this column's value off a row. Attribute access only — see the module note."""
        value: Any = path
        for part in self.attribute.split("."):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value


COLUMNS: tuple[Column, ...] = (
    # --- Identity -----------------------------------------------------------
    Column(
        "code",
        "identity",
        "code",
        label="Satnet Path",
        render="link",
        order_by="code",
        default=True,
    ),
    Column(
        "satnet", "identity", "satnet.code", label="Satnet", order_by="satnet__code", default=True
    ),
    Column("beam", "identity", "beam.code", label="Beam", order_by="beam__code", default=True),
    Column("hub", "identity", "satnet.hub.code", label="Hub", order_by="satnet__hub__code"),
    Column(
        "direction", "identity", "direction", label="Direction", order_by="direction", default=True
    ),
    Column(
        "revision",
        "identity",
        "revision_number",
        label="Revision",
        render="number",
        order_by="revision_number",
    ),
    # --- Lifecycle ----------------------------------------------------------
    Column(
        "status",
        "lifecycle",
        "status",
        label="Status",
        render="status",
        order_by="status",
        default=True,
    ),
    Column(
        "valid_from",
        "lifecycle",
        "valid_from",
        label="Valid from",
        render="utc",
        order_by="valid_from",
        default=True,
    ),
    Column(
        "valid_until",
        "lifecycle",
        "valid_until",
        label="Valid until",
        render="utc",
        order_by="valid_until",
    ),
    Column(
        "updated_at",
        "lifecycle",
        "updated_at",
        label="Last changed",
        render="utc",
        order_by="updated_at",
    ),
    # --- What was asked for -------------------------------------------------
    Column(
        "symbol_rate",
        "request",
        "symbol_rate_sps",
        spec_code="SYMBOL_RATE",
        render="number",
        order_by="symbol_rate_sps",
    ),
    Column("rolloff", "request", "rolloff", spec_code="ROLLOFF", order_by="rolloff"),
    # --- Bandwidth ----------------------------------------------------------
    Column(
        "occupied_bw",
        "bandwidth",
        "occupied_bw_hz",
        spec_code="OCCUPIED_BANDWIDTH",
        render="mhz",
        order_by="occupied_bw_hz",
        default=True,
    ),
    Column(
        "allocated_bw",
        "bandwidth",
        "allocated_bw_hz",
        spec_code="ALLOCATED_BANDWIDTH",
        render="mhz",
        order_by="allocated_bw_hz",
    ),
    Column(
        "guard_left",
        "bandwidth",
        "guard_left_hz",
        spec_code="LEFT_GUARD",
        render="mhz",
        order_by="guard_left_hz",
    ),
    Column(
        "guard_right",
        "bandwidth",
        "guard_right_hz",
        spec_code="RIGHT_GUARD",
        render="mhz",
        order_by="guard_right_hz",
    ),
    # --- Canonical side -----------------------------------------------------
    Column(
        "canonical_leg",
        "canonical",
        "canonical_leg",
        label="Canonical leg",
        order_by="canonical_leg",
    ),
    Column(
        "canonical_start",
        "canonical",
        "canonical_allocated_start_hz",
        spec_code="FWD_HUB_UL_START_RF",
        render="mhz",
        order_by="canonical_allocated_start_hz",
        default=True,
    ),
    Column(
        "canonical_center",
        "canonical",
        "canonical_center_hz",
        spec_code="FWD_HUB_UL_CENTER_RF",
        render="mhz",
        order_by="canonical_center_hz",
    ),
    Column(
        "canonical_end",
        "canonical",
        "canonical_allocated_end_hz",
        spec_code="FWD_HUB_UL_END_RF",
        render="mhz",
        order_by="canonical_allocated_end_hz",
        default=True,
    ),
    Column(
        "canonical_polarization",
        "canonical",
        "canonical_polarization",
        label="Polarization",
        order_by="canonical_polarization",
    ),
    # --- Translated side ----------------------------------------------------
    Column(
        "translated_leg",
        "translated",
        "translated_leg",
        label="Translated leg",
        order_by="translated_leg",
    ),
    Column(
        "translated_center",
        "translated",
        "translated_center_hz",
        spec_code="FWD_REMOTE_DL_CENTER_RF",
        render="mhz",
        order_by="translated_center_hz",
    ),
    Column(
        "translated_start",
        "translated",
        "translated_allocated_start_hz",
        label="Translated start",
        render="mhz",
        order_by="translated_allocated_start_hz",
    ),
    Column(
        "translated_end",
        "translated",
        "translated_allocated_end_hz",
        label="Translated end",
        render="mhz",
        order_by="translated_allocated_end_hz",
    ),
    # --- Hardware -----------------------------------------------------------
    Column("gateway", "hardware", "gateway.code", label="GW ID", order_by="gateway__code"),
    Column(
        "decimator",
        "hardware",
        "decimator_assignment.decimator.code",
        label="Decimator",
        order_by="decimator_assignment__decimator__code",
    ),
)

BY_KEY: dict[str, Column] = {column.key: column for column in COLUMNS}

#: What a table shows to somebody who has chosen nothing. Deliberately narrow: §10.3 wants a
#: readable table, and a default that shows everything teaches an operator to ignore the
#: column picker rather than to use it.
DEFAULT_KEYS: tuple[str, ...] = tuple(column.key for column in COLUMNS if column.default)


def resolve(keys: list[str] | tuple[str, ...] | None) -> list[Column]:
    """Turn stored column keys into columns, in the registry's order.

    Unknown keys are **dropped, not an error**: a saved view outlives the column it names, and
    an operator whose view was saved before a column was renamed should get their table back
    minus one column rather than a stack trace.
    """
    wanted = set(keys or DEFAULT_KEYS)
    chosen = [column for column in COLUMNS if column.key in wanted]
    return chosen or [BY_KEY[key] for key in DEFAULT_KEYS]


def grouped() -> list[tuple[str, str, list[Column]]]:
    """Every column, grouped, for the picker."""
    return [
        (key, title, [column for column in COLUMNS if column.group == key]) for key, title in GROUPS
    ]


def ordering_for(sort: str) -> list[str]:
    """Turn a ``sort`` parameter into a queryset ordering.

    A leading ``-`` reverses. An unknown or unsortable column falls back to the default
    ordering rather than raising: the sort arrives from a URL, and a URL is user input.
    """
    descending = sort.startswith("-")
    column = BY_KEY.get(sort.lstrip("-"))
    if column is None or not column.sortable:
        return ["satnet__code", "code"]
    return [f"{'-' if descending else ''}{column.order_by}"]
