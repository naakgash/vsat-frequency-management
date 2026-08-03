"""Report drift between the code registry and the Specification Dictionary.

Two failure modes this catches, both silent otherwise:

* a code referenced by application logic with no dictionary row, which renders as a bare
  code with no explanation;
* a dictionary row with no description, which is an unanswered engineering question
  (specification section 26.20) rather than a finished entry.

Run by the release flow and available to an administrator on demand.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from specifications.models import SpecificationDefinition
from specifications.registry import SYSTEM_CODES


class Command(BaseCommand):
    help = "Check the Specification Dictionary against the code registry."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when any specification is missing a description.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        stored = dict(SpecificationDefinition.objects.values_list("code", "description"))

        missing_rows = sorted(SYSTEM_CODES - set(stored))
        orphaned = sorted(
            code
            for code in stored
            if code not in SYSTEM_CODES
            and SpecificationDefinition.objects.get(code=code).is_system_managed
        )
        undescribed = sorted(code for code, text in stored.items() if not (text or "").strip())

        if missing_rows:
            self.stdout.write(
                self.style.ERROR(
                    f"{len(missing_rows)} registered code(s) have no dictionary row: "
                    f"{', '.join(missing_rows)}\nRun `manage.py migrate` to seed them."
                )
            )

        if orphaned:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(orphaned)} row(s) are marked system-managed but are not in the "
                    f"registry: {', '.join(orphaned)}"
                )
            )

        if undescribed:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(undescribed)} specification(s) await engineering input:\n  "
                    + "\n  ".join(undescribed)
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Every specification has a description."))

        if missing_rows or (options["strict"] and undescribed):
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"{len(stored)} specification(s) in the dictionary."))
