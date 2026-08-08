"""The acceptance checklist is a set of claims, so the claims are checked. §26, §26.20.

`docs/acceptance-checklist.md` says which §26 criteria are met and names the test that evidences
each one. A document like that decays in a specific way: the criterion stays, the test it names
gets renamed or deleted, and the row goes on saying "Met" for years. By the time anybody checks,
the checklist is the only record of a guarantee nobody has.

So every reference in it is verified here — the evidence files exist, the commits exist, the
statuses come from a fixed vocabulary, and nothing claims `Met` with no evidence at all.

**The last test is the gate itself.** The slice plan says the register must be empty of §3.1
items before the application becomes the source of truth, and this asserts that the checklist's
summary of what is outstanding matches what `operations.acceptance` reads out of the register.
A checklist that could quietly declare the gate closed would be the single most dangerous
document in the repository.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from operations import acceptance

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs/acceptance-checklist.md"

#: Every §26 criterion. Twenty, and the checklist must account for all of them — a criterion
#: that is simply absent is the easiest way for a gap to go unreported.
CRITERIA = tuple(f"26.{number}" for number in range(1, 21))

#: The statuses a row may carry. A fixed vocabulary so that "mostly done" and "essentially met"
#: cannot appear: §27 says a failing criterion is reported as failing, not softened.
ALLOWED_STATUSES = (
    "Met",
    "Met in code, unproven against reality",
    "Partial",
    "Blocked",
    "Met as a discipline; the gate is open",
)


def rows() -> dict[str, dict[str, str]]:
    """The checklist's criteria table, parsed into `{criterion: {status, evidence, commit}}`."""
    parsed: dict[str, dict[str, str]] = {}
    for line in CHECKLIST.read_text().splitlines():
        match = re.match(r"\|\s*(26\.\d+)\s*\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|", line)
        if not match:
            continue
        parsed[match.group(1)] = {
            "criterion": match.group(2).strip(),
            "status": _plain(match.group(3)),
            "evidence": match.group(4).strip(),
            "commit": match.group(5).strip(),
        }
    return parsed


def _plain(cell: str) -> str:
    return re.sub(r"[*`]", "", cell).strip()


def _paths(cell: str) -> list[str]:
    return re.findall(r"`([^`]+\.py)`", cell)


def _commits(cell: str) -> list[str]:
    return re.findall(r"`([0-9a-f]{7,40})`", cell)


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------
def test_the_checklist_exists():
    """S18's named deliverable. Its absence is the failure this whole file guards against."""
    assert CHECKLIST.exists(), (
        "docs/acceptance-checklist.md is missing. It is S18's deliverable: one row per §26 "
        "criterion with pass/fail and the evidence for it."
    )


def test_every_criterion_has_exactly_one_row():
    """A criterion that is simply absent is the easiest way for a gap to go unreported."""
    parsed = rows()

    missing = [criterion for criterion in CRITERIA if criterion not in parsed]
    assert not missing, f"No row for: {', '.join(missing)}"
    assert set(parsed) == set(CRITERIA), f"Unexpected rows: {set(parsed) - set(CRITERIA)}"


@pytest.mark.parametrize("criterion", CRITERIA)
def test_every_status_comes_from_the_fixed_vocabulary(criterion):
    """§27: a failing criterion is reported as failing, not softened. A free-text status is
    where "essentially met" and "met pending review" come from."""
    status = rows()[criterion]["status"]

    assert status in ALLOWED_STATUSES, (
        f"§{criterion} has status {status!r}. Allowed: {', '.join(ALLOWED_STATUSES)}. "
        f"If none fits, the honest move is a new status here, not a softer sentence there."
    )


# ---------------------------------------------------------------------------
# The evidence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("criterion", CRITERIA)
def test_every_evidence_file_exists(criterion):
    """The decay this file exists to stop: the test is renamed, the row still says Met."""
    row = rows()[criterion]

    for reference in _paths(row["evidence"]):
        assert (ROOT / reference).exists(), (
            f"§{criterion} cites {reference}, which does not exist. Either the evidence moved "
            f"and this row must follow it, or the guarantee is gone and the status is wrong."
        )


@pytest.mark.parametrize("criterion", CRITERIA)
def test_a_met_criterion_carries_evidence(criterion):
    """`Met` with nothing behind it is an assertion, and the point of the document is that it
    contains none."""
    row = rows()[criterion]
    if not row["status"].startswith("Met"):
        return

    assert _paths(row["evidence"]), (
        f"§{criterion} claims {row['status']!r} and cites no test. A criterion with no evidence "
        f"cannot claim to be met."
    )


@pytest.mark.parametrize("criterion", CRITERIA)
def test_every_named_commit_is_real(criterion):
    """A commit that does not exist is a citation nobody can follow."""
    for revision in _commits(rows()[criterion]["commit"]):
        result = subprocess.run(  # noqa: S603 - a hex string matched from the document
            ["git", "cat-file", "-t", revision],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == "commit", (
            f"§{criterion} cites commit {revision}, which is not in this history."
        )


def test_the_evidence_files_are_tests_rather_than_anything_else():
    """Evidence has to be something that *fails* when the claim stops being true. A source
    file or a screenshot cannot do that; a test can."""
    for criterion in CRITERIA:
        for reference in _paths(rows()[criterion]["evidence"]):
            assert reference.startswith("tests/"), (
                f"§{criterion} cites {reference} as evidence. Evidence must be a test — "
                f"something that fails when the claim does."
            )


# ---------------------------------------------------------------------------
# The gate — §26.20
# ---------------------------------------------------------------------------
def test_the_gate_is_evaluated_from_the_register_rather_than_the_checklist():
    """The two must agree, and the register is the one that wins.

    If they could disagree, the checklist would be the more comfortable document to edit.
    """
    gate = acceptance.evaluate(with_database=False)
    text = CHECKLIST.read_text()

    for question in gate.outstanding:
        assert question.identifier in text, (
            f"{question.identifier} is outstanding in the register and is not named in the "
            f"acceptance checklist. Every unresolved RF value has to appear in the document "
            f"somebody reads before a cutover."
        )


def test_the_checklist_does_not_claim_a_gate_that_is_open():
    """§26.20's status must not read as unqualified `Met` while RF values are outstanding.

    The single most dangerous edit anybody could make to this repository is the one that turns
    §26.20 green while the register is still full, so it is the one thing asserted twice.
    """
    gate = acceptance.evaluate(with_database=False)
    status = rows()["26.20"]["status"]

    if gate.outstanding or not gate.golden_examples:
        assert status != "Met", (
            f"§26.20 is marked Met, but {len(gate.outstanding)} RF engineering value(s) are "
            f"outstanding and {gate.golden_examples} golden worked example(s) exist. The "
            f"discipline may be met; the gate is not, and the status has to say so."
        )


def test_the_golden_directory_is_still_empty_and_the_checklist_agrees():
    """Not a permanent truth — a *current* one, and this test is how the day it changes gets
    noticed. When a worked example arrives this fails, and the right response is to update the
    checklist and set VSAT_REQUIRE_GOLDEN_EXAMPLES=1 in CI."""
    examples = acceptance.count_golden_examples()

    if examples:
        pytest.fail(
            f"{examples} golden worked example(s) have appeared. OQ-22 may now be closable. "
            f"Two things follow: amend docs/acceptance-checklist.md, and turn on "
            f"VSAT_REQUIRE_GOLDEN_EXAMPLES=1 in CI so tests/domain/test_golden_examples.py "
            f"becomes a hard failure rather than a skip."
        )
    assert "OQ-22" in CHECKLIST.read_text()


def test_the_summary_counts_add_up():
    """A summary that disagrees with its own table is the version people quote."""
    parsed = rows()
    counted = sum(1 for row in parsed.values() if row["status"] == "Met")
    text = CHECKLIST.read_text()

    match = re.search(r"Criteria fully met \| \*\*(\d+)\*\*", text)
    assert match, "The summary has no 'Criteria fully met' figure."
    assert int(match.group(1)) == counted, (
        f"The summary says {match.group(1)} criteria are fully met; the table has {counted}."
    )
