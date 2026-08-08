"""May this platform become the source of truth? §26.20, §24.

Run before a cutover, and expect it to say no until the RF engineering values have arrived and
a worked example has been supplied. **A non-zero exit is the normal, correct answer today** —
the inventory ships empty because every value it would hold is an unresolved question, and this
command is what stops "the application is finished" being mistaken for "the application is the
record".
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from operations import acceptance


class Command(BaseCommand):
    help = "Report whether the platform may replace the spreadsheets (section 26.20)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--json", action="store_true", help="Emit the result for a monitor to read."
        )
        parser.add_argument(
            "--no-database",
            action="store_true",
            help="Read the register and the golden examples only; do not count rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        gate = acceptance.evaluate(with_database=not options["no_database"])

        if options["json"]:
            self.stdout.write(json.dumps(gate.as_dict(), indent=2))
        else:
            self._report(gate)

        if not gate.ok:
            raise CommandError(
                f"{len(gate.outstanding)} RF engineering value(s) outstanding and "
                f"{gate.golden_examples} golden worked example(s) supplied. This platform must "
                f"not become the source of truth yet — see docs/rf-confirmation/ for how the "
                f"values are asked for, and docs/acceptance-checklist.md for what that blocks."
            )
        self.stdout.write(self.style.SUCCESS("The gate is clear."))

    def _report(self, gate: acceptance.Gate) -> None:
        self.stdout.write(
            self.style.SUCCESS("Golden worked examples: 1 or more")
            if gate.golden_examples
            else self.style.ERROR(
                "Golden worked examples: none. OQ-22 cannot be closed by building — section 24 "
                "asks for a real operational Satnet Path calculated independently by an RF "
                "engineer, and anything this implementation produces proves only that it "
                "agrees with itself."
            )
        )

        if not gate.outstanding:
            self.stdout.write(self.style.SUCCESS("Every section 3.1 open question is settled."))
            return

        self.stdout.write("")
        self.stdout.write(f"{len(gate.outstanding)} RF engineering value(s) outstanding:")
        for question in gate.outstanding:
            state = "answered, no rows loaded" if question.answered else "unanswered"
            lands = ", ".join(question.lands_in) or "—"
            self.stdout.write(self.style.WARNING(f"  {question.identifier}  ({state})"))
            self.stdout.write(f"      {question.question}")
            self.stdout.write(f"      lands in: {lands}")
