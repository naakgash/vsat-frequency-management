"""One of seven outcomes per row, most blocking first. §17.1, `docs/design/02` §8.

The order the checks run in is the whole design. A row can be several of these at once — a
free-capacity row with an unknown Satnet label and a missing roll-off is all three — and the one
it is reported as decides what somebody does about it. Reported as ``NEEDS_MAPPING``, they go and
create a mapping for a row that should never have been read at all.

So:

1. :data:`IGNORED_FREE_CAPACITY` — first, and before anything is required of the row. A gap is
   not a badly filled-in allocation.
2. :data:`ERROR` — a cell could not be read, or a required one is empty.
3. :data:`NEEDS_MAPPING` — everything read, but a label names nothing.
4. :data:`DUPLICATE` — this allocation is already here, or is in this file twice.
5. :data:`CONFLICT` — it recalculates, and the spectrum it asks for is taken.
6. :data:`WARNING` — it recalculates, and the file disagrees with the engine about a derived
   value. Committed, with the disagreement recorded.
7. :data:`VALID` — nothing to say.

**Nothing here writes.** Classification is the dry run, and a dry run touches no production data
(§17.1) — it reads master data, reads reservations, and calls
``satnet_paths.services.preview``, which computes without saving by construction (§9.3).
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from imports_exports.constants import RowClassification
from imports_exports.importer import fields as field_registry
from imports_exports.importer import mapping
from imports_exports.importer.normalize import Message, NormalizedRow
from satnet_paths import services as path_services
from satnet_paths.models import SatnetPath

#: What the incumbent spreadsheet writes in an identity cell to mean "this row is spare
#: spectrum, not a transmission". §17.1 requires these rows to be ignored rather than imported,
#: and ADR-0009 says why they can never be imported: free capacity is *computed* from what is
#: allocated, so a stored free-capacity row would be a second source of truth for the one figure
#: §16 forbids storing — and worse, importing it as an allocation would reserve the very
#: spectrum it says is free.
#:
#: **Provisional, and tied to OQ-18.** These are the phrases such a sheet plausibly uses; the
#: real list can only come from the real workbook. Getting one wrong is safe in one direction
#: (an unrecognised marker becomes an ERROR row somebody reads) and not in the other, which is
#: why the list is short and exact rather than a pattern.
FREE_CAPACITY_MARKERS = frozenset(
    {
        "free",
        "free capacity",
        "available",
        "available capacity",
        "spare",
        "spare capacity",
        "unused",
        "unallocated",
        "vacant",
        "gap",
    }
)


@dataclasses.dataclass
class Judgement:
    """What a row is, and everything to say about it."""

    classification: str
    messages: list[Message] = dataclasses.field(default_factory=list)
    #: The Satnet Path this row refers to, when it refers to one that already exists.
    existing_id: uuid.UUID | None = None
    #: Resolved references, so a commit does not repeat the lookups.
    resolved: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def committable(self) -> bool:
        from imports_exports.constants import COMMITTABLE

        return self.classification in COMMITTABLE


@dataclasses.dataclass
class Seen:
    """What earlier rows in this file already claimed.

    Duplicate detection has to look inside the file as well as at the database: a spreadsheet
    listing the same allocation twice would otherwise commit the first, then fail the second on
    a unique constraint — which is a stack trace where a sentence belongs.
    """

    identities: set[uuid.UUID] = dataclasses.field(default_factory=set)
    codes: set[tuple[str, str]] = dataclasses.field(default_factory=set)


def judge(raw: dict[str, Any], normalized: NormalizedRow, seen: Seen) -> Judgement:
    """Classify one row. Pure of writes; see the module note."""
    if is_free_capacity(raw, normalized):
        return Judgement(
            RowClassification.IGNORED_FREE_CAPACITY,
            [
                Message(
                    "FREE_CAPACITY",
                    "This row describes spare spectrum rather than a transmission, so it is "
                    "ignored. Free capacity is computed from what is allocated (ADR-0009); "
                    "importing it would reserve the spectrum it says is free.",
                )
            ],
        )

    if not normalized.ok:
        return Judgement(RowClassification.ERROR, list(normalized.errors))

    resolved, unresolved = _references(normalized)
    if unresolved:
        return Judgement(RowClassification.NEEDS_MAPPING, unresolved)

    duplicate = _duplicate(normalized, resolved, seen)
    if duplicate is not None:
        return duplicate

    return _recalculate(normalized, resolved)


def is_free_capacity(raw: dict[str, Any], normalized: NormalizedRow) -> bool:
    """Is this row about spare spectrum rather than about a transmission? §17.1.

    Two ways of saying so, because a spreadsheet says it both ways: a marker word in the Satnet
    Path or Satnet cell, and a row that carries a frequency but names nothing at all. The second
    matters more than it looks — a gap row in a hand-maintained sheet is very often just a range
    with the identity columns left blank.
    """
    identity_headings = (
        field_registry.BY_KEY["code"].heading,
        field_registry.BY_KEY["satnet"].heading,
    )
    labels = [str(raw.get(heading, "") or "").strip().lower() for heading in identity_headings]

    if any(label in FREE_CAPACITY_MARKERS for label in labels):
        return True
    named = any(labels)
    return not named and "canonical_center_hz" in normalized.values


# ---------------------------------------------------------------------------
# The individual checks
# ---------------------------------------------------------------------------
def _references(normalized: NormalizedRow) -> tuple[dict[str, Any], list[Message]]:
    resolved: dict[str, Any] = {}
    unresolved: list[Message] = []

    for field in field_registry.INPUT_FIELDS:
        if not field.reference or field.key not in normalized.values:
            continue
        resolution = mapping.resolve(field.reference, str(normalized.values[field.key]))
        if resolution.ok:
            resolved[field.key] = resolution.target
        else:
            unresolved.append(Message("NEEDS_MAPPING", resolution.reason, field.key))
    return resolved, unresolved


def _duplicate(normalized: NormalizedRow, resolved: dict[str, Any], seen: Seen) -> Judgement | None:
    """Is this allocation already here? §17.1's idempotency, and the file's own repeats.

    An identifier that already exists is a **re-import**, not a second allocation: the row is
    reported as a duplicate and points at what it matched, which is what makes committing the
    same file twice a no-op rather than a way to double every allocation in the plan.
    """
    satnet = resolved.get("satnet")
    code = str(normalized.values.get("code", ""))
    key = (str(getattr(satnet, "pk", "")), code)

    identifier = normalized.identity.get("id")
    if identifier is not None:
        if identifier in seen.identities:
            return Judgement(
                RowClassification.DUPLICATE,
                [
                    Message(
                        "DUPLICATE_IN_FILE",
                        f"The identifier {identifier} appears earlier in this file.",
                        "id",
                    )
                ],
            )
        existing = SatnetPath.objects.filter(pk=identifier).first()
        if existing is not None:
            return Judgement(
                RowClassification.DUPLICATE,
                [
                    Message(
                        "ALREADY_PRESENT",
                        f"This allocation is already here as {existing.code} under "
                        f"{existing.satnet.code}. Re-importing a file does not create it twice.",
                        "id",
                    )
                ],
                existing_id=existing.pk,
            )

    if key in seen.codes:
        return Judgement(
            RowClassification.DUPLICATE,
            [
                Message(
                    "DUPLICATE_IN_FILE",
                    f"{code!r} under this Satnet appears earlier in this file.",
                    "code",
                )
            ],
        )

    if satnet is not None:
        current = (
            SatnetPath.objects.filter(satnet=satnet, code=code, superseded_by__isnull=True)
            .only("id")
            .first()
        )
        if current is not None:
            return Judgement(
                RowClassification.DUPLICATE,
                [
                    Message(
                        "ALREADY_PRESENT",
                        f"{code!r} already exists under {satnet.code}. An import creates "
                        f"allocations; changing one is a revision, which is a decision somebody "
                        f"makes on the record itself (§15.4).",
                        "code",
                    )
                ],
                existing_id=current.pk,
            )
    return None


def _recalculate(normalized: NormalizedRow, resolved: dict[str, Any]) -> Judgement:
    """Run the row through the engine and see what comes back. §17.1.

    This is where "never trusts an Excel-calculated value" stops being a policy and becomes the
    code: the only values handed to ``preview`` are the operator's inputs, and every edge,
    bandwidth and guard in the result is computed here and now against current master data.
    """
    satnet = resolved["satnet"]
    values = normalized.values

    try:
        proposal = path_services.preview(
            satnet=satnet,
            direction=values["direction"],
            input_mode=values["input_mode"],
            input_value=values["input_value"],
            rolloff=values["rolloff"],
            centre_hz=values["canonical_center_hz"],
            valid_from=values["valid_from"],
            valid_until=values.get("valid_until"),
        )
    except Exception as exc:  # a missing direction config, an impossible request, a bad rolloff
        return Judgement(
            RowClassification.ERROR,
            [
                Message(
                    "NOT_CALCULABLE",
                    f"This row could not be calculated: {exc}",
                )
            ],
            resolved=resolved,
        )

    if proposal.findings:
        return Judgement(
            RowClassification.CONFLICT,
            [Message(finding.code, finding.message) for finding in proposal.findings],
            resolved=resolved,
        )

    disagreements = _disagreements(normalized, proposal)
    if disagreements:
        return Judgement(RowClassification.WARNING, disagreements, resolved=resolved)
    return Judgement(RowClassification.VALID, resolved=resolved)


def _disagreements(normalized: NormalizedRow, proposal: Any) -> list[Message]:
    """Where the file's arithmetic and the engine's differ. §17.1, §26.16.

    The engine wins — always, and without a setting to change that. What the file said is
    reported so somebody can find out *why* it differs, because a spreadsheet that has been
    right for years and is suddenly 250 kHz out is usually a guard policy nobody wrote down
    rather than a bug in either of them.

    Only the values a proposal states directly are compared. The band edges follow from them
    arithmetically, so re-deriving the edges here to compare them would be checking this
    module's arithmetic against the engine's rather than the file's against the engine's.
    """
    left, right = proposal.guard_widths.left_hz, proposal.guard_widths.right_hz
    computed = {
        "occupied_bw_hz": proposal.occupied_bw_hz,
        "symbol_rate_sps": proposal.symbol_rate_sps,
        "guard_left_hz": left,
        "guard_right_hz": right,
        "allocated_bw_hz": proposal.occupied_bw_hz + left + right,
    }
    messages = []
    for attribute, claimed in sorted(normalized.claimed.items()):
        expected = computed.get(attribute)
        if expected is None or expected == claimed:
            continue
        messages.append(
            Message(
                "RECALCULATED",
                f"The file says {attribute} is {claimed}; this platform calculates "
                f"{expected}. The calculated value is what will be stored — an import never "
                f"takes a value a spreadsheet worked out (§17.1).",
                attribute,
            )
        )
    return messages
