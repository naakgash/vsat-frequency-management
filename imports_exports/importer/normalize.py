"""Cells to the platform's own types, or to a reason why not. §17.1, **A-08**, **A-28**.

Every conversion here is **strict and unit-free**. A cell reading ``29145`` is 29 145 hertz, not
29 145 megahertz, because the platform's unit is the hertz (**A-08**) and the export writes
integer hertz. A cell reading ``29145 MHz`` is refused rather than converted: guessing which
unit a spreadsheet meant is exactly the kind of invention §26.20 forbids, and being wrong by a
factor of a million produces an allocation that looks plausible and is nowhere near the right
frequency.

**A formula is never evaluated.** `parse` opens the workbook so that a formula cell yields its
formula text; this module refuses that text with the formula quoted in the message, so the
person fixing it can see what the cell held. §17.1's "never trusts an Excel-calculated value" is
these two facts together: the cached value is never read, and the formula is never run.

**A naive timestamp is UTC.** The xlsx format cannot carry a time zone, so the export strips the
zone and states it in the heading (``Valid from (UTC)``). Reading one back has to make the same
assumption in the same direction, and ADR-0022 makes UTC the platform's only display zone, so
there is exactly one right answer and it is stated here rather than inferred per caller.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import re
import uuid
from decimal import Decimal
from typing import Any

from imports_exports.export.safety import restore
from imports_exports.importer import fields as field_registry

#: What a stored formula begins with. Only ``=``, and that is not a simplification: Excel accepts
#: ``+``, ``-`` and ``@`` as ways of *typing* a formula and rewrites every one of them to ``=``
#: before storing it. A cell whose stored text begins with ``-`` is therefore text — which is
#: exactly the case §21.12 guards on the way out and :func:`_literal` unguards on the way back.
FORMULA_PREFIX = "="

#: Digits, with the separators a person or a locale might have left in them. A thousands
#: separator is forgiven because a spreadsheet shows one whether or not it stored one; a decimal
#: point on a frequency is not, because a fraction of a hertz is not a thing this platform holds.
_SEPARATORS = re.compile(r"[\s,_]")


@dataclasses.dataclass(frozen=True)
class Message:
    """One thing to say about a row, addressed to whoever has to fix it."""

    code: str
    text: str
    field: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "text": self.text, "field": self.field}


@dataclasses.dataclass
class NormalizedRow:
    """What a row was understood to mean, and everything unclear about it."""

    values: dict[str, Any] = dataclasses.field(default_factory=dict)
    #: ``id`` and ``revision_group`` as read, when the file carries them.
    identity: dict[str, uuid.UUID] = dataclasses.field(default_factory=dict)
    #: The derived columns the file claimed, for comparison against what the engine computes.
    claimed: dict[str, int] = dataclasses.field(default_factory=dict)
    errors: list[Message] = dataclasses.field(default_factory=list)
    warnings: list[Message] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_json(self) -> dict[str, Any]:
        """A JSON-storable view, for ``import_row.normalized``.

        Values are rendered rather than stored raw because the column is JSONB: a `Decimal` and
        a `datetime` have no JSON representation, and the point of the column is that somebody
        can read it later and see what the importer thought the row said.
        """
        return {
            **{key: _jsonable(value) for key, value in self.values.items()},
            **{key: str(value) for key, value in self.identity.items()},
        }


def row(raw: dict[str, Any]) -> NormalizedRow:
    """Interpret one row of cells, keyed by heading."""
    result = NormalizedRow()

    for field in field_registry.INPUT_FIELDS:
        cell = raw.get(field.heading)
        if _is_blank(cell):
            if field.required:
                result.errors.append(
                    Message(
                        "MISSING",
                        f"{field.heading!r} is required and this row leaves it empty.",
                        field.key,
                    )
                )
            continue

        formula = _formula_in(cell)
        if formula:
            # §17.1. Not evaluated, and the cached value openpyxl could have handed over was
            # never asked for — see `parse.read`.
            result.errors.append(
                Message(
                    "FORMULA",
                    f"{field.heading!r} holds the formula {formula!r}. An import reads values, "
                    f"never formulas, and never the answer a spreadsheet cached for one. "
                    f"Replace it with the value it should produce.",
                    field.key,
                )
            )
            continue

        try:
            result.values[field.key] = _convert(field, _literal(cell))
        except ValueError as exc:
            result.errors.append(Message("UNREADABLE", str(exc), field.key))

    result.identity = _identity(raw, result)
    result.claimed = _claimed(raw, result)
    return result


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------
def _convert(field: field_registry.Field, cell: Any) -> Any:
    if field.kind == "text":
        return str(cell).strip()
    if field.kind == "choice":
        return _choice(field, cell)
    if field.kind == "integer":
        return _integer(field.heading, cell)
    if field.kind == "decimal":
        return _decimal(field.heading, cell)
    if field.kind == "utc":
        return _utc(field.heading, cell)
    raise ValueError(f"{field.heading!r} has no reader for kind {field.kind!r}.")


def _choice(field: field_registry.Field, cell: Any) -> str:
    """Match a choice, ignoring case and surrounding space, and nothing else.

    Not a fuzzy match. ``forward`` is not ``FWD``: the two are the same word to a person and a
    different allocation to the platform, and an importer that guessed would put a transmission
    on the wrong leg of the payload.
    """
    candidate = str(cell).strip().upper()
    if candidate in field.choices:
        return candidate
    allowed = ", ".join(field.choices)
    raise ValueError(f"{field.heading!r} reads {cell!r}. It has to be one of: {allowed}.")


def _integer(heading: str, cell: Any) -> int:
    """A whole number, in the platform's own unit. **A-08**."""
    if isinstance(cell, bool):
        raise ValueError(f"{heading!r} reads {cell!r}, which is not a number.")
    if isinstance(cell, int):
        return cell
    if isinstance(cell, float):
        if not cell.is_integer():
            raise ValueError(
                f"{heading!r} reads {cell!r}. Frequencies and bandwidths are whole hertz "
                f"(A-08); a fraction of a hertz is not a value this platform holds."
            )
        return int(cell)
    if isinstance(cell, Decimal):
        if cell != cell.to_integral_value():
            raise ValueError(f"{heading!r} reads {cell!r}, which is not a whole number.")
        return int(cell)

    text = _SEPARATORS.sub("", str(cell))
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError(
            f"{heading!r} reads {str(cell)!r}. It has to be a whole number of hertz, with no "
            f"unit — this platform stores hertz (A-08), and a value carrying its own unit "
            f"would have to be guessed at."
        )
    return int(text)


def _decimal(heading: str, cell: Any) -> Decimal:
    """A Decimal, kept exact. ADR-0003.

    Converted from ``str(cell)`` even when the cell already holds a float, because
    ``Decimal(0.35)`` is 0.34999999999999997779553950749686919152736663818359375 and a roll-off
    is a value somebody typed to two places.
    """
    if isinstance(cell, Decimal):
        return cell
    try:
        return Decimal(str(cell).strip())
    except (decimal.InvalidOperation, ValueError) as exc:
        raise ValueError(f"{heading!r} reads {str(cell)!r}, which is not a number.") from exc


def _utc(heading: str, cell: Any) -> datetime.datetime:
    """A timestamp in UTC. **A-28**, ADR-0022 — see the module note for the naive case."""
    if isinstance(cell, datetime.datetime):
        moment = cell
    elif isinstance(cell, datetime.date):
        # A date cell is midnight. Stated rather than implied: a validity that started "on the
        # 3rd" starts at 00:00 on the 3rd, and half-open ranges (ADR-0008) make that the
        # inclusive edge.
        moment = datetime.datetime.combine(cell, datetime.time.min)
    else:
        text = str(cell).strip().replace("Z", "+00:00")
        try:
            moment = datetime.datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"{heading!r} reads {str(cell)!r}. A timestamp has to be a date cell or an ISO "
                f"8601 string such as 2026-08-08T12:00:00Z, and it is read as UTC."
            ) from exc

    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.UTC)
    return moment.astimezone(datetime.UTC)


# ---------------------------------------------------------------------------
# Identity and claims
# ---------------------------------------------------------------------------
def _identity(raw: dict[str, Any], result: NormalizedRow) -> dict[str, uuid.UUID]:
    """``id`` and ``revision_group``, when the file carries them.

    An unreadable identifier is an **error**, not something to ignore: a row whose id is
    mistyped would otherwise be created as a new allocation alongside the one it was meant to
    be, which is the duplicate an import must never make silently.
    """
    found: dict[str, uuid.UUID] = {}
    for key in field_registry.IDENTITY_FIELDS:
        cell = raw.get(key)
        if _is_blank(cell):
            continue
        try:
            found[key] = uuid.UUID(str(cell).strip())
        except ValueError:
            result.errors.append(
                Message(
                    "BAD_IDENTIFIER",
                    f"{key!r} reads {str(cell)!r}, which is not an identifier this platform "
                    f"issued. Leave it empty for a new allocation, or paste the value from an "
                    f"export.",
                    key,
                )
            )
    return found


def _claimed(raw: dict[str, Any], result: NormalizedRow) -> dict[str, int]:
    """The derived values the file asserts, so they can be checked against the engine.

    Read, never used (§17.1, §26.16). A cell that cannot be read as a number is skipped in
    silence: it is not an input, so refusing the row over it would block an import on a column
    the importer was going to ignore anyway.
    """
    claimed: dict[str, int] = {}
    for heading, attribute in field_registry.derived_headings().items():
        cell = raw.get(heading)
        if _is_blank(cell) or _formula_in(cell):
            continue
        try:
            claimed[attribute] = _integer(heading, cell)
        except ValueError:
            continue
    return claimed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _is_blank(cell: Any) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def _formula_in(cell: Any) -> str:
    """The formula this cell holds, or an empty string.

    ``parse`` hands over formula *text* because the workbook is opened with ``data_only=False``:
    the cached answer is never asked for, so this is the only thing a formula cell can be.
    """
    if isinstance(cell, str) and cell.startswith(FORMULA_PREFIX) and len(cell) > 1:
        return cell
    return ""


def _literal(cell: Any) -> Any:
    """A cell's value with §21.12's guard removed, once it is known not to be a formula."""
    return restore(cell)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
