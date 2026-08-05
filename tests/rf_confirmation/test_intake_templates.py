"""The S0 intake package stays true to the models it will be loaded into. Section 24.

``docs/design/05`` requires each workbook to carry *"the exact columns the eventual import
expects"*. That is a promise about the future, and the only way to keep it is to check it
on every commit: a sheet that was right when it was written and wrong after the next
migration is worse than no sheet at all, because the mismatch surfaces only after somebody
has entered four hundred rows under headings that no longer exist.

These tests run in both directions.

* **Forward** — the committed files are exactly what the generator produces, so a model
  change that is not reflected in the package fails here rather than in a spreadsheet.
* **Backward** — every field an importer would have to be given a value for is covered by
  some column, or is listed as deliberately not collected with a reason.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from django.conf import settings

from inventory import intake
from inventory.management.commands import export_intake_templates as exporter

PACKAGE_ROOT = Path(settings.BASE_DIR) / exporter.PACKAGE_DIR
REGISTER = Path(settings.BASE_DIR) / "docs/design/00-assumptions-and-open-questions.md"


def _sheet_ids(sheet: intake.Sheet) -> str:
    return sheet.slug


# ---------------------------------------------------------------------------
# Forward: the committed package matches the models
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_a_committed_sheet_matches_what_the_generator_produces(sheet: intake.Sheet):
    """The drift guard. Regenerate with `python manage.py export_intake_templates`."""
    path = PACKAGE_ROOT / "templates" / sheet.filename

    assert path.exists(), f"{sheet.filename} is missing from the intake package."
    # open(newline="") rather than read_text: Path.read_text only grew a newline argument
    # in 3.13, and without it the CRLF the sheet is written with is translated away on
    # read, so the comparison would pass whatever the file actually contains.
    with path.open(encoding="utf-8", newline="") as handle:
        committed = handle.read()

    assert committed == exporter.sheet_csv(sheet), (
        f"{sheet.filename} no longer matches the model it will be loaded into. "
        f"Run: python manage.py export_intake_templates"
    )


def test_the_column_guide_matches_the_sheets():
    guide = PACKAGE_ROOT / "column-guide.md"

    assert guide.read_text(encoding="utf-8") == exporter.guide_markdown(), (
        "The column guide is stale. Run: python manage.py export_intake_templates"
    )


# ---------------------------------------------------------------------------
# Backward: every field the importer needs is asked for
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_every_required_field_is_either_collected_or_explained(sheet: intake.Sheet):
    """A required field that no column asks for makes the sheet unloadable.

    This is the test that bites when a later slice adds a mandatory column to a model. It
    fails on the *model* change, which is when somebody can still decide whether the new
    field is RF engineering's to answer or the platform's to write.
    """
    asked_for = {column.field or column.lookup for column in sheet.columns}
    explained = set(sheet.supplied_by_the_platform)

    missing = [
        model_field.name
        for model_field in intake.importable_fields(sheet.model)
        if model_field.name not in asked_for and model_field.name not in explained
    ]

    assert not missing, (
        f"{sheet.title} would not load: {sorted(missing)} are required by "
        f"{sheet.model.__name__} but no column collects them. Either add a column, or list "
        f"the field in supplied_by_the_platform with the reason it is not asked for."
    )


@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_nothing_is_excused_that_is_no_longer_required(sheet: intake.Sheet):
    """A stale exclusion is as misleading as a missing column.

    If a field stops being required — gains a default, becomes nullable — its entry in
    ``supplied_by_the_platform`` still reads as a considered decision about a constraint
    that no longer exists.
    """
    required = {model_field.name for model_field in intake.importable_fields(sheet.model)}

    stale = sorted(set(sheet.supplied_by_the_platform) - required)

    assert not stale, (
        f"{sheet.title} excuses {stale}, which {sheet.model.__name__} no longer requires. "
        f"Remove the entry, or collect the column."
    )


@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_every_column_names_a_real_field(sheet: intake.Sheet):
    for column in sheet.columns:
        name = column.field or column.lookup
        if name is None:
            continue  # A context column, documented as answering no field of its own.
        sheet.model._meta.get_field(name)  # Raises FieldDoesNotExist if it has gone.


@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_a_lookup_column_points_at_a_relation(sheet: intake.Sheet):
    """A lookup is resolved by the target's code, so the target has to have one."""
    for column in sheet.columns:
        if not column.lookup:
            continue
        related = sheet.model._meta.get_field(column.lookup).related_model
        assert related is not None, f"{column.heading} is a lookup but {column.lookup} is not a FK."
        assert related._meta.get_field("code"), f"{related.__name__} has no code to look up by."


# ---------------------------------------------------------------------------
# The package ships empty
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_a_sheet_ships_with_no_data(sheet: intake.Sheet):
    """§26.20. The container ships; the value does not.

    An example row would be loaded by somebody in a hurry and would then be
    indistinguishable from a figure RF engineering confirmed.
    """
    path = PACKAGE_ROOT / "templates" / sheet.filename
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert len(rows) == 1, (
        f"{sheet.filename} carries {len(rows) - 1} data rows. It must ship empty."
    )


def test_the_golden_example_template_ships_empty():
    template = json.loads((PACKAGE_ROOT / "templates" / exporter.GOLDEN_FILE.name).read_text())

    assert template["source"] == ""
    assert template["uplink_centre_hz"] is None
    assert template["expect"]["uplink_occupied"] == [None, None]


def test_the_golden_example_template_asks_for_what_the_answer_requires():
    """**A-29**. The template is the only place an engineer sees what is being asked for.

    OQ-22's answer widened the request considerably — window, assignment, conversion rule,
    equipment limits, validity, free capacity and three reuse outcomes — and a template still
    asking only for bandwidths would produce an example that passes every arithmetic test and
    closes nothing. The completeness check on a *submitted* file lives in
    ``tests/domain/test_golden_examples.py``; this is its counterpart on the blank form.
    """
    from tests.domain import test_golden_examples as harness

    template = json.loads((PACKAGE_ROOT / "templates" / exporter.GOLDEN_FILE.name).read_text())

    missing = [section for section in harness.REQUIRED_SECTIONS if section not in template]
    assert not missing, f"The golden-example template does not ask for {missing}."
    assert "free_capacity" in template["expect"]
    assert template["expect"]["scenarios"], (
        "The template carries no scenario shape, so nobody filling it in would know the three "
        "reuse outcomes are part of what closes OQ-22."
    )


def test_the_golden_example_template_has_the_keys_the_harness_reads():
    """The same drift risk as the CSVs, in a file no model can generate.

    ``tests/domain/test_golden_examples.py`` reads a fixed set of keys. A template with a
    key it renamed would be filled in correctly and then ignored — the worst outcome, since
    the example would appear to pass. Filling the template with placeholder numbers and
    running it through the harness's own loader proves the shapes still agree. Nothing is
    asserted about the *results*: these numbers are scaffolding, not a worked example.
    """
    from tests.domain import test_golden_examples as harness

    template = json.loads((PACKAGE_ROOT / "templates" / exporter.GOLDEN_FILE.name).read_text())
    filled = {
        **template,
        "source": "placeholder",
        "rolloff": "0.2",
        "symbol_rate_sps": 1_000_000,
        "uplink_centre_hz": 12_000_000_000,
        "guard": {**template["guard"], "mode": "FIXED", "left_hz": 0, "right_hz": 0},
        "translation": {**template["translation"], "method": "OFFSET_ADD", "constant_hz": 0},
    }
    del filled["occupied_bandwidth_hz"]  # The request takes one input mode or the other.

    harness._run(filled)  # A KeyError here means the template and the harness have parted.


# ---------------------------------------------------------------------------
# The package points at the register it exists to close
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sheet", intake.SHEETS, ids=_sheet_ids)
def test_every_sheet_cites_an_open_question_that_exists(sheet: intake.Sheet):
    """A sheet collecting an answer to a question nobody asked is a sheet nobody needs."""
    register = REGISTER.read_text(encoding="utf-8")

    assert sheet.open_questions, f"{sheet.title} states no open question it answers."
    for code in sheet.open_questions:
        assert f"**{code}**" in register, (
            f"{sheet.title} cites {code}, which is not in the register."
        )
