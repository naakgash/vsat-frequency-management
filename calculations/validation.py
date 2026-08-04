"""Placement checks that run before anything reaches the database.

Specification section 12 requires validation at the interface, the service and the
database. This is the first of the three: it explains *why* something will be refused,
while the database only refuses. Neither replaces the other — §8.3 keeps the exclusion
constraint as the authority, and a check that passes here still has to pass there.

Every check returns findings rather than raising. A wizard needs to show an operator
everything wrong with a placement at once; raising on the first problem turns one screen
into four.
"""

from __future__ import annotations

import dataclasses
import enum

from calculations.conversion import ProfileMatch
from calculations.ranges import FrequencyRange
from calculations.translation import TranslationSpec
from calculations.types import Placement, TwoSidedPlacement


class Severity(enum.StrEnum):
    """Whether a finding blocks a placement or merely warns about it.

    The distinction matters more than it looks. §26.20 forbids inventing RF rules, and
    several checks here rest on values that are still open questions. Reporting those as
    warnings keeps the platform usable while they are unanswered, and keeps the fact that
    they are unanswered visible instead of buried in a passing check.
    """

    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclasses.dataclass(frozen=True)
class Finding:
    """One thing wrong, or possibly wrong, with a placement."""

    code: str
    severity: Severity
    message: str
    #: Specification section or open question this finding comes from, for the interface
    #: to cite. An unexplained refusal is one an operator works around.
    reference: str = ""

    @property
    def blocks(self) -> bool:
        return self.severity is Severity.ERROR


def check_placement(
    placement: Placement,
    *,
    window: FrequencyRange | None = None,
    min_edge_guard_hz: int = 0,
    band: FrequencyRange | None = None,
) -> list[Finding]:
    """Every check that applies to a single placement, in one pass.

    ``window`` is the Frequency Window the placement must fit inside — the authoritative
    grant of permission to allocate (§13.2 makes Band limits informative by comparison, and
    ``band`` is checked separately and only warns for exactly that reason).
    """
    findings: list[Finding] = []
    findings += _check_internal_consistency(placement)
    if window is not None:
        findings += _check_window_containment(placement, window, min_edge_guard_hz)
    if band is not None:
        findings += _check_band_advisory(placement, band)
    return findings


def check_two_sided(
    placement: TwoSidedPlacement,
    *,
    uplink_window: FrequencyRange | None = None,
    downlink_window: FrequencyRange | None = None,
    uplink_edge_guard_hz: int = 0,
    downlink_edge_guard_hz: int = 0,
) -> list[Finding]:
    """Both legs of a Satnet Path, checked together. ADR-0006.

    Checking them separately would miss the thing that matters most: a transmission that
    fits its uplink Window perfectly and falls outside the downlink Window is not placeable,
    and the uplink check alone says it is. §8.1 makes *both* reservations exclusive, so both
    have to hold.

    Finding codes are prefixed with the leg, because "OUTSIDE_WINDOW" on a two-sided result
    does not say which window.
    """
    findings: list[Finding] = []
    findings += _prefixed(
        "UPLINK",
        check_placement(
            placement.uplink, window=uplink_window, min_edge_guard_hz=uplink_edge_guard_hz
        ),
    )
    findings += _prefixed(
        "DOWNLINK",
        check_placement(
            placement.downlink,
            window=downlink_window,
            min_edge_guard_hz=downlink_edge_guard_hz,
        ),
    )

    if not placement.widths_agree:
        findings.append(
            Finding(
                code="SIDES_DISAGREE",
                severity=Severity.ERROR,
                message=(
                    f"The uplink reserves {placement.uplink.allocated.width_hz} Hz and the "
                    f"downlink reserves {placement.downlink.allocated.width_hz} Hz. A "
                    f"translation preserves width, so the two sides were computed "
                    f"independently rather than one from the other."
                ),
                reference="section 13.7, ADR-0006",
            )
        )

    return findings


def check_translation(spec: TranslationSpec) -> list[Finding]:
    """Is the Payload Path's own arithmetic self-consistent?

    Separate from the placement checks because it is a property of the *master data*, not
    of any one transmission: the same finding applies to every allocation on that path, and
    reporting it once against the path is more useful than repeating it per placement.
    """
    if spec.is_contradictory:
        return [
            Finding(
                code="INVERSION_WITHOUT_REFLECTION",
                severity=Severity.ERROR,
                message=(
                    f"Payload path {spec.label or spec.method} is recorded as inverting the "
                    f"spectrum, but {spec.method} is an offset and preserves the order of "
                    f"frequencies. An inverting path needs a reflection constant; there is "
                    f"no way to compute the inversion from an offset, so the translation "
                    f"cannot be trusted either way."
                ),
                reference="section 13.7, OQ-02",
            )
        ]
    return []


def check_conversion(match: ProfileMatch, *, band: FrequencyRange | None = None) -> list[Finding]:
    """An equipment profile evaluated against an RF interval. Specification section 13.5."""
    findings: list[Finding] = []

    if not match.is_usable:
        findings.append(
            Finding(
                code="NO_EQUIPMENT_MATCH",
                severity=Severity.ERROR,
                message=(
                    f"Profile {match.profile.code} cannot carry this transmission: "
                    f"{match.rejected_because}."
                ),
                reference="section 13.5",
            )
        )
        return findings

    assert match.intermediate is not None  # guaranteed by is_usable
    if band is not None and not band.contains(match.intermediate):
        findings.append(
            Finding(
                code="IF_OUTSIDE_BAND",
                severity=Severity.WARNING,
                message=(
                    f"The intermediate frequency {match.intermediate} falls outside the "
                    f"expected IF band {band}. The profile's own limits accept it, and they "
                    f"are the authority — this is a flag for review."
                ),
                reference="section 13.5",
            )
        )

    if match.profile.inverts:
        findings.append(
            Finding(
                code="EQUIPMENT_INVERTS",
                severity=Severity.WARNING,
                message=(
                    f"Profile {match.profile.code} inverts the spectrum, so the low edge of "
                    f"the RF interval becomes the high edge of the IF interval. This is "
                    f"normal for high-side injection and is reported so a plot is not read "
                    f"the wrong way round."
                ),
                reference="section 13.5, A-10",
            )
        )

    return findings


def _prefixed(leg: str, findings: list[Finding]) -> list[Finding]:
    return [dataclasses.replace(f, code=f"{leg}_{f.code}") for f in findings]


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.blocks]


def is_placeable(findings: list[Finding]) -> bool:
    return not blocking(findings)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _check_internal_consistency(placement: Placement) -> list[Finding]:
    """Invariants the engine itself must never break.

    These should be unreachable through :func:`calculations.bandwidth.place`. They are
    checked anyway because a ``Placement`` can also be rebuilt from stored columns — by the
    importer, or when re-displaying an allocation made under an earlier version of the
    engine — and a row that no longer satisfies them is exactly what someone needs to be
    told about.
    """
    findings = []

    if not placement.allocated.contains(placement.occupied):
        findings.append(
            Finding(
                code="OCCUPIED_OUTSIDE_ALLOCATED",
                severity=Severity.ERROR,
                message=(
                    f"The occupied range {placement.occupied} is not inside the allocated "
                    f"range {placement.allocated}. The reservation would be narrower than "
                    f"the transmission."
                ),
                reference="section 8.1",
            )
        )

    if placement.occupied.width_hz < placement.occupied_bandwidth_hz:
        findings.append(
            Finding(
                code="OCCUPIED_RANGE_TOO_NARROW",
                severity=Severity.ERROR,
                message=(
                    f"The occupied range spans {placement.occupied.width_hz} Hz but the "
                    f"occupied bandwidth is {placement.occupied_bandwidth_hz} Hz. Rounding "
                    f"is outward (A-09), so this can never be short."
                ),
                reference="A-09",
            )
        )

    if placement.guards.total_hz == 0:
        findings.append(
            Finding(
                code="NO_GUARD_APPLIED",
                severity=Severity.WARNING,
                message=(
                    "No guard band is applied, so this placement may sit immediately "
                    "against its neighbour. Adjacency is legal, but a zero guard is "
                    "usually a missing policy rather than a decision — guard values by "
                    "band, window and platform are unconfirmed (OQ-07)."
                ),
                reference="section 25, OQ-07",
            )
        )

    return findings


def _check_window_containment(
    placement: Placement, window: FrequencyRange, min_edge_guard_hz: int
) -> list[Finding]:
    """The allocation must fit inside its Frequency Window. Specification section 13.6."""
    findings = []

    if not window.contains(placement.allocated):
        findings.append(
            Finding(
                code="OUTSIDE_WINDOW",
                severity=Severity.ERROR,
                message=(
                    f"The allocated range {placement.allocated} does not fit inside the "
                    f"Frequency Window {window}. A Window is what grants permission to "
                    f"allocate; spectrum outside one is not available."
                ),
                reference="section 13.6",
            )
        )
        return findings  # The edge-guard check below is meaningless once it is outside.

    if min_edge_guard_hz > 0:
        # OQ-34 asks whether the minimum edge guard forms part of the allocated range or is
        # a separate validation. The design's provisional position is *separate*: the
        # database enforces containment only, and this check enforces the standoff. Making
        # it an ERROR would settle the question by implication, so it warns and cites it.
        low = placement.allocated.start_hz - window.start_hz
        high = window.end_hz - placement.allocated.end_hz
        if low < min_edge_guard_hz or high < min_edge_guard_hz:
            findings.append(
                Finding(
                    code="INSIDE_EDGE_GUARD",
                    severity=Severity.WARNING,
                    message=(
                        f"The allocation sits {min(low, high)} Hz from a Window edge, "
                        f"inside the {min_edge_guard_hz} Hz minimum edge guard. Whether "
                        f"the edge guard is part of the allocated range or a separate "
                        f"standoff is unconfirmed (OQ-34), so this is reported rather "
                        f"than refused."
                    ),
                    reference="section 13.6, OQ-34",
                )
            )

    return findings


def _check_band_advisory(placement: Placement, band: FrequencyRange) -> list[Finding]:
    """Band limits are informative, not authoritative. Specification section 13.2.

    A warning rather than an error, and that is the specification's choice, not a
    hedge: §13.2 states that the Frequency Window is what authorises an allocation, and
    Band bounds exist to describe the band. Refusing here would let a Band record that is
    merely out of date block spectrum a Window explicitly grants.
    """
    if band.contains(placement.allocated):
        return []
    return [
        Finding(
            code="OUTSIDE_BAND",
            severity=Severity.WARNING,
            message=(
                f"The allocated range {placement.allocated} extends beyond the Band "
                f"{band}. Band limits are informative — the Frequency Window is what "
                f"authorises the allocation — so this is a flag, not a refusal."
            ),
            reference="section 13.2",
        )
    ]
