"""May this platform become the source of truth? §26.20, §24.

The question S18 exists to answer, and the one it must not answer optimistically. §26.20 says
no RF value may be invented and every unresolved rule must be a recorded `OPEN QUESTION`; the
slice plan's gate adds that **the register must be empty of §3.1 items** — the RF engineering
values — before the application replaces the spreadsheets.

**Everything here is derived, never asserted.** The open questions are read out of
`docs/design/00`'s own §3.1 table, the golden examples are counted in the directory that would
hold them, and the inventory counts come from the database. A hand-maintained "we are ready"
flag is exactly the thing that goes stale the week before a cutover, so there isn't one.

**Empty inventory is not a defect.** It is §26.20 being obeyed: every value those tables would
hold is an unresolved RF question, and a plausible-looking invented one would be
indistinguishable from real data once loaded. What this module reports is that the platform is
*correct and not yet usable as the record* — two different things, and conflating them is how a
cutover happens before the data is real.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "docs/design/00-assumptions-and-open-questions.md"
GOLDEN = ROOT / "tests/domain/golden"

#: The §3.1 heading, and the one after it. The table between them is the gate's whole input.
SECTION_START = "### 3.1 Blocking for production activation"
SECTION_END = "### 3.2"

#: Where each §3.1 answer lands, as `app_label.ModelName`. Read to report whether the answer has
#: actually arrived as data rather than merely as an email — which is the difference between a
#: question being answered and a platform being usable.
LANDING_TABLES: dict[str, tuple[str, ...]] = {
    "OQ-01": ("inventory.FrequencyWindow",),
    "OQ-02": ("inventory.PayloadPath",),
    "OQ-03": ("inventory.PayloadPolarizationMapping",),
    "OQ-04": ("inventory.EquipmentProfile",),
    "OQ-06": ("inventory.RolloffOption",),
    "OQ-07": ("inventory.GuardPolicy",),
    "OQ-14": ("inventory.Band",),
    "OQ-24": ("spectrum.SpectrumReservation",),
}


@dataclasses.dataclass(frozen=True)
class OpenQuestion:
    """One §3.1 item, and whether its answer has arrived."""

    identifier: str
    question: str
    lands_in: tuple[str, ...]
    answered: bool
    rows: int | None = None

    @property
    def satisfied(self) -> bool:
        """Answered *and* the data is there.

        Both halves. An answer that has been given and not loaded leaves the platform in
        exactly the state it was in before — which is the state this gate exists to detect.
        """
        return self.answered and bool(self.rows)


@dataclasses.dataclass
class Gate:
    """Whether the platform may replace the spreadsheets, and what is stopping it."""

    open_questions: list[OpenQuestion] = dataclasses.field(default_factory=list)
    golden_examples: int = 0

    @property
    def outstanding(self) -> list[OpenQuestion]:
        return [question for question in self.open_questions if not question.satisfied]

    @property
    def ok(self) -> bool:
        """§26.20's gate: no outstanding RF value, and at least one golden worked example.

        The golden example is called out separately from the register even though OQ-22 is in
        it, because **OQ-22 cannot be closed by building** (§24): anything this implementation
        produces proves the implementation against itself. Only a file from an RF engineer
        closes it, and counting the files is the only honest way to ask.
        """
        return not self.outstanding and self.golden_examples > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "golden_examples": self.golden_examples,
            "outstanding": [
                {
                    "id": question.identifier,
                    "question": question.question,
                    "lands_in": list(question.lands_in),
                    "answered": question.answered,
                    "rows": question.rows,
                }
                for question in self.outstanding
            ],
        }


def evaluate(*, with_database: bool = True) -> Gate:
    """Read the register, count what has landed, and report. Never writes anything."""
    questions = []
    for identifier, text, answered in _register_items():
        lands_in = LANDING_TABLES.get(identifier, ())
        rows = _row_count(lands_in) if (with_database and lands_in) else None
        questions.append(
            OpenQuestion(
                identifier=identifier,
                question=text,
                lands_in=lands_in,
                answered=answered,
                rows=rows,
            )
        )
    return Gate(open_questions=questions, golden_examples=count_golden_examples())


def count_golden_examples() -> int:
    """Worked examples in `tests/domain/golden/`, not counting the note explaining the gap.

    **OQ-22**, and the one thing in this product that cannot be closed by building. §24 asks
    for a real operational Satnet Path calculated independently by an RF engineer; anything the
    implementation produces proves it self-consistent and nothing more.
    """
    if not GOLDEN.is_dir():
        return 0
    return len(
        [
            path
            for path in GOLDEN.iterdir()
            if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml", ".toml"}
        ]
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _register_items() -> list[tuple[str, str, bool]]:
    """The §3.1 table, parsed out of the register itself.

    Parsed rather than restated. A second copy of this list in Python would be a second answer
    to "what is still missing", and the two would part company on the day one of them was
    updated — which is the day it matters most.
    """
    text = REGISTER.read_text()
    start = text.find(SECTION_START)
    if start == -1:
        raise RuntimeError(
            f"{REGISTER.name} has no {SECTION_START!r} section. The acceptance gate reads its "
            f"input from that table; if it has moved, this must follow it rather than guess."
        )
    end = text.find(SECTION_END, start)
    section = text[start : end if end != -1 else len(text)]

    items = []
    for line in section.splitlines():
        match = re.match(r"\|\s*\*\*(OQ-\d+)\*\*\s*\|(.+?)\|", line)
        if not match:
            continue
        identifier, question = match.group(1), match.group(2).strip()
        # The register marks a settled question by striking the text through and writing
        # ANSWERED into the row. Nothing in §3.1 is marked that way today, and the parser has
        # to notice on the day one is.
        answered = "ANSWERED" in question or question.startswith("~~")
        items.append((identifier, _plain(question), answered))
    return items


def _plain(text: str) -> str:
    """The question without its markdown, short enough to print in a table."""
    stripped = re.sub(r"[*~`]", "", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped if len(stripped) <= 110 else stripped[:107] + "…"


def _row_count(labels: tuple[str, ...]) -> int:
    """How many rows have landed across the tables an answer lands in."""
    from django.apps import apps

    total = 0
    for label in labels:
        try:
            model = apps.get_model(label)
        except LookupError:  # pragma: no cover - a renamed model is a code change, not a state
            continue
        total += model.objects.count()
    return total
