"""The shape of the RF confirmation intake sheets. Slice S0, specification section 24.

Phase 0 of the roadmap exists to turn the `OPEN QUESTION` register into something RF
engineering can actually fill in. The hard requirement on that package is in
``docs/design/05``: each workbook must carry *"the exact columns the eventual import
expects"*. A hand-typed column list would satisfy that on the day it was written and
quietly stop being true at the next migration — and the failure is expensive in a way that
most drift is not, because it surfaces after somebody has spent a week entering four
hundred rows under the wrong headings.

So the sheets are **declared against the models** and generated from them. A column names a
model field; its unit, whether it is required, and its permitted values are read from that
field rather than restated here. ``tests/rf_confirmation`` then asserts the reverse
direction — that every field an importer would have to supply is covered by some column —
so adding a required column to :class:`~inventory.models.FrequencyWindow` without extending
its sheet fails the build.

**Nothing in this module contains an RF value**, and the generated sheets have no data
rows. That is the point of the slice: the containers ship, the answers do not (§26.20).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any

from django.db import models

from inventory.models import (
    Band,
    EquipmentProfile,
    FrequencyWindow,
    GuardPolicy,
    PayloadPath,
    PayloadPolarizationMapping,
    SpectrumResource,
)

if TYPE_CHECKING:
    from django.db.models import Field as ModelField


@dataclass(frozen=True)
class Column:
    """One column of an intake sheet.

    Exactly one of three shapes:

    * ``field`` — a direct model field. Unit, requiredness and allowed values are read
      from it.
    * ``lookup`` — a foreign key supplied by the target's human-readable code rather than
      by UUID. RF engineering has codes; it does not have our primary keys.
    * neither — a **context** column that disambiguates a lookup (a Payload Path code is
      only unique within its Satellite) or collects a child table. It answers no model
      field of its own, so it is excluded from the coverage check.
    """

    heading: str
    field: str | None = None
    lookup: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.field and self.lookup:
            raise ValueError(f"{self.heading}: a column is either a field or a lookup, not both")


@dataclass(frozen=True)
class Sheet:
    """One intake workbook: a model, the columns to collect, and what it answers."""

    slug: str
    title: str
    model: type[models.Model]
    open_questions: tuple[str, ...]
    purpose: str
    columns: tuple[Column, ...]
    #: Required model fields deliberately absent from the sheet, each with the reason.
    #: Stated rather than implied, so the coverage test can tell a considered omission
    #: from a forgotten column.
    supplied_by_the_platform: dict[str, str] = dataclass_field(default_factory=dict)

    @property
    def filename(self) -> str:
        return f"{self.slug}.csv"


# ---------------------------------------------------------------------------
# Reading a field's contract off the model
# ---------------------------------------------------------------------------
#: Fields every record carries for bookkeeping. Never collected on an intake sheet: they
#: are written by the platform at import, not supplied by the person filling it in.
PLATFORM_MANAGED = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "record_version",
        "is_active",
        "version_group",
        "version_number",
        "supersedes",
        "effective_period",
    }
)


def importable_fields(model: type[models.Model]) -> list[ModelField[Any, Any]]:
    """The fields an importer would have to be given a value for.

    Concrete, editable, no usable default, and not one of the bookkeeping columns. A field
    with a default is genuinely optional on a sheet — leaving ``min_edge_guard_hz`` blank
    means zero, which is a real answer — so defaults are excluded rather than demanded.
    """
    result: list[ModelField[Any, Any]] = []
    for candidate in model._meta.get_fields():
        # Reverse relations come back from ``get_fields`` as ``ForeignObjectRel``, which is
        # not a ``Field`` and holds no value of its own.
        if not isinstance(candidate, models.Field):
            continue
        if candidate.name in PLATFORM_MANAGED or not candidate.editable:
            continue
        if candidate.has_default() or candidate.null or candidate.blank:
            continue
        result.append(candidate)
    return result


def unit_of(model_field: ModelField[Any, Any]) -> str:
    """How a value is written in the cell.

    Derived from the field rather than restated, because the sheet's whole purpose is to
    match the column it will be loaded into. ``_hz`` suffixes are a naming convention this
    codebase holds everywhere (**A-08**), so reading the unit off the name is reliable
    here in a way it would not be in a general-purpose tool.
    """
    if model_field.choices:
        return "one of the permitted values"
    if model_field.name.endswith("_hz"):
        return "Hz, whole number"
    if model_field.name.startswith("percent_"):
        return "percent, up to 3 decimal places"
    if isinstance(model_field, models.DateTimeField):
        return "ISO 8601 UTC, for example 2026-01-01T00:00:00Z"
    if isinstance(model_field, models.BooleanField):
        return "true or false"
    if isinstance(model_field, models.DecimalField):
        return f"decimal, up to {model_field.decimal_places} decimal places"
    if isinstance(model_field, models.IntegerField):
        return "whole number"
    if isinstance(model_field, models.TextField):
        return "free text"
    max_length = getattr(model_field, "max_length", None)
    return f"text, up to {max_length} characters" if max_length else "text"


def allowed_values_of(model_field: ModelField[Any, Any]) -> str:
    """The permitted values, when the field constrains them.

    Comma-separated rather than pipe-separated: the guide renders these inside a Markdown
    table cell, and a pipe would end the cell and shift every column after it.
    """
    if not model_field.choices:
        return ""
    return ", ".join(str(value) for value, _ in model_field.choices)


def is_required(model_field: ModelField[Any, Any]) -> bool:
    return not (model_field.null or model_field.blank or model_field.has_default())


# ---------------------------------------------------------------------------
# The sheets
# ---------------------------------------------------------------------------
# docs/design/05 names six subjects. A seventh — Bands — is included because **OQ-14**
# (which polarizations are in use) and **OQ-31** (whether a tuning raster exists) are RF
# facts with no other container: a Band's identity is administrative, but those two
# attributes are not, and without a sheet they would have to be guessed at data-entry time.
#
# Satellites, Gateways and Hubs deliberately have **no** sheet. They are administrative
# records entered through the application, and nothing about them is an unanswered RF
# question. The sheets reference them by code and the import resolves them.

SHEETS: tuple[Sheet, ...] = (
    Sheet(
        slug="01-bands",
        title="Bands",
        model=Band,
        open_questions=("OQ-14", "OQ-31"),
        purpose=(
            "Band identity is administrative, but two of its attributes are not. Which "
            "polarization types are actually in use is OQ-14, and whether modems tune on a "
            "raster is OQ-31 — without which Auto-place will propose centre frequencies no "
            "modem can be configured to."
        ),
        columns=(
            Column("code", field="code"),
            Column("name", field="name"),
            Column("rf_min_hz", field="rf_min_hz"),
            Column("rf_max_hz", field="rf_max_hz"),
            Column(
                "allowed_polarizations",
                note=(
                    "OQ-14. Semicolon-separated, from the permitted values of the "
                    "polarization columns on the other sheets. Leave blank if unconfirmed "
                    "— no Band ships with any preselected."
                ),
            ),
            Column("tuning_raster_hz", field="tuning_raster_hz"),
            Column("default_display_unit", field="default_display_unit"),
            Column("description", field="description"),
        ),
    ),
    Sheet(
        slug="02-frequency-windows",
        title="Frequency Windows",
        model=FrequencyWindow,
        open_questions=("OQ-01", "OQ-07", "OQ-34"),
        purpose=(
            "The authoritative grant of permission to allocate spectrum. Band limits are "
            "informative (§13.2); an allocation must fit inside a Window. One row is one "
            "leg of one satellite at one polarization — two polarizations on the same leg "
            "are two rows, because §25 makes them separable only as separate Windows."
        ),
        columns=(
            Column("satellite_code", lookup="satellite"),
            Column("band_code", lookup="band"),
            Column("code", field="code"),
            Column("name", field="name"),
            Column("side", field="side"),
            Column("polarization", field="polarization"),
            Column(
                "rf_start_hz",
                field="rf_start_hz",
                note="Inclusive lower edge. Ranges are half-open, [start, end).",
            ),
            Column(
                "rf_end_hz",
                field="rf_end_hz",
                note=(
                    "Exclusive upper edge. A window ending at 14500000000 does not include "
                    "that Hz; a neighbour may start there (A-11)."
                ),
            ),
            Column(
                "min_edge_guard_hz",
                field="min_edge_guard_hz",
                note="OQ-34. Leave blank for none — blank means zero, not unknown.",
            ),
            Column(
                "default_guard_policy_code",
                lookup="default_guard_policy",
                note="From sheet 05. Leave blank if the Window has no default.",
            ),
            Column("effective_from", field="effective_from"),
            Column("effective_until", field="effective_until"),
            Column(
                "source_reference",
                field="source_reference",
                note="Where this figure came from. Not ceremony — see the README.",
            ),
            Column("description", field="description"),
        ),
    ),
    Sheet(
        slug="03-payload-translations",
        title="Payload translations",
        model=PayloadPath,
        open_questions=("OQ-02",),
        purpose=(
            "How the satellite maps an uplink frequency to its downlink frequency. §13.7 "
            "requires the mapping to be deterministic, which is why the method and constant "
            "are collected rather than inferred from the two windows' edges: any two windows "
            "sit at some offset from each other, and reading the relationship off them would "
            "produce a plausible number that is not the payload's actual translation."
        ),
        columns=(
            Column("satellite_code", lookup="satellite"),
            Column("code", field="code"),
            Column("name", field="name"),
            Column("direction", field="direction"),
            Column(
                "uplink_window_code",
                lookup="uplink_window",
                note="From sheet 02, on the same Satellite.",
            ),
            Column(
                "downlink_window_code",
                lookup="downlink_window",
                note="From sheet 02, on the same Satellite.",
            ),
            Column("translation_method", field="translation_method"),
            Column(
                "translation_constant_hz",
                field="translation_constant_hz",
                note=(
                    "The offset for OFFSET_ADD and OFFSET_SUBTRACT; the reflection constant "
                    "K for LO_REFLECT, where downlink = K - uplink."
                ),
            ),
            Column(
                "spectral_inversion",
                field="spectral_inversion",
                note=(
                    "LO_REFLECT inverts by construction and needs no flag here. Set this "
                    "only for a path that inverts for some other reason."
                ),
            ),
            Column("effective_from", field="effective_from"),
            Column("effective_until", field="effective_until"),
            Column("engineering_reference", field="engineering_reference"),
            Column("description", field="description"),
        ),
        supplied_by_the_platform={
            "uplink_window_side": (
                "Denormalised from the uplink Window so a composite foreign key can prove "
                "it matches the direction. Written by the import, never entered — a typed "
                "value could disagree with the Window it came from."
            ),
            "downlink_window_side": "As above, from the downlink Window.",
        },
    ),
    Sheet(
        slug="04-polarization-mappings",
        title="Polarization mappings",
        model=PayloadPolarizationMapping,
        open_questions=("OQ-03",),
        purpose=(
            "Which uplink/downlink polarization pairs each Payload Path permits. The table "
            "ships empty: a plausible default — RHCP up, RHCP down — would be "
            "indistinguishable from a confirmed one once loaded."
        ),
        columns=(
            Column(
                "satellite_code",
                note="Context. A Payload Path code is only unique within its Satellite.",
            ),
            Column("payload_path_code", lookup="payload_path", note="From sheet 03."),
            Column("uplink_polarization", field="uplink_polarization"),
            Column("downlink_polarization", field="downlink_polarization"),
        ),
    ),
    Sheet(
        slug="05-guard-policies",
        title="Guard policies",
        model=GuardPolicy,
        open_questions=("OQ-07",),
        purpose=(
            "The separation applied either side of a transmission, by Band, Window and "
            "platform. Required separation must be expressed as a guard and never as an "
            "implicit gap: half-open ranges make touching allocations legal (A-11)."
        ),
        columns=(
            Column("code", field="code"),
            Column("name", field="name"),
            Column(
                "mode",
                field="mode",
                note=(
                    "The mode decides which of the four value columns are required. A "
                    "policy missing the values its own mode needs is refused by the "
                    "database rather than resolving to a zero guard."
                ),
            ),
            Column("fixed_left_hz", field="fixed_left_hz"),
            Column("fixed_right_hz", field="fixed_right_hz"),
            Column("percent_left", field="percent_left"),
            Column("percent_right", field="percent_right"),
            Column("description", field="description"),
        ),
    ),
    Sheet(
        slug="06-spectrum-resources",
        title="Spectrum resources",
        model=SpectrumResource,
        open_questions=("OQ-25",),
        purpose=(
            "What competes with what. The overlap guarantee is judged on these rows: two "
            "allocations conflict when they occupy the same resource with overlapping RF and "
            "overlapping time, and nothing else about them matters. A leg mapped to no "
            "resource competes with nothing, so this sheet is the difference between a "
            "platform that prevents interference and one that merely records it."
        ),
        columns=(
            Column("satellite_code", lookup="satellite"),
            Column("code", field="code"),
            Column("name", field="name"),
            Column("kind", field="kind"),
            Column("leg", field="leg"),
            Column(
                "polarization",
                field="polarization",
                note=(
                    "Leave blank when both polarizations share the RF chain and therefore "
                    "compete. Set it only where the chains are independently implemented."
                ),
            ),
            Column("effective_from", field="effective_from"),
            Column(
                "effective_until",
                field="effective_until",
                note=(
                    "Leave blank for a fixed payload. A software-defined payload's resources "
                    "are time-bounded, so this is where a reconfiguration is recorded."
                ),
            ),
            Column(
                "source_reference",
                field="source_reference",
                note="The approved frequency and polarization plan this resource comes from.",
            ),
            Column("description", field="description"),
        ),
    ),
    Sheet(
        slug="07-equipment-profiles",
        title="Equipment profiles",
        model=EquipmentProfile,
        open_questions=("OQ-04", "OQ-26"),
        purpose=(
            "BUC, BDC and LNB conversion limits by site and model. These decide both the "
            "L-band IF the platform reports and which profiles it will accept for a given "
            "RF, so an approximate LO produces a confidently wrong IF."
        ),
        columns=(
            Column("code", field="code"),
            Column("name", field="name"),
            Column("type", field="type"),
            Column("band_code", lookup="band", note="From sheet 01."),
            Column("vendor", field="vendor"),
            Column("model", field="model"),
            Column("rf_min_hz", field="rf_min_hz"),
            Column("rf_max_hz", field="rf_max_hz"),
            Column("if_min_hz", field="if_min_hz"),
            Column("if_max_hz", field="if_max_hz"),
            Column("lo_hz", field="lo_hz"),
            Column("conversion_method", field="conversion_method"),
            Column(
                "sideband",
                field="sideband",
                note=(
                    "Must agree with the method: LO_PLUS_IF is low-side, LO_MINUS_IF is "
                    "high-side. The pairing is what makes IF = |RF - LO| invertible, so the "
                    "database enforces it."
                ),
            ),
            Column("spectral_inversion", field="spectral_inversion"),
            Column(
                "priority",
                field="priority",
                note="Lower sorts first when several profiles are valid. Blank means 100.",
            ),
            Column(
                "label",
                field="label",
                note=(
                    "LOW, MID and HIGH may be used here as labels. They must never drive "
                    "branching logic (§13.5), and nothing in the platform reads this field."
                ),
            ),
            Column(
                "gateway_code",
                lookup="gateway",
                note="Leave blank when the profile applies everywhere.",
            ),
            Column("hub_code", lookup="hub", note="Leave blank when it applies to every Hub."),
            Column("effective_from", field="effective_from"),
            Column("effective_until", field="effective_until"),
            Column("engineering_reference", field="engineering_reference"),
            Column("description", field="description"),
        ),
    ),
)
