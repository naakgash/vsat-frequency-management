"""Validity containment for a Satnet Path. **OQ-32**, ADR-0020.

    *"A Satnet Path's active period must be fully contained within: its Satnet's validity
    period; its Beam's validity period; and the validity period of the Beam Spectrum
    Assignment referenced by the Path… The service shall reject an operational Path whose
    requested period extends beyond that intersection. It shall identify the limiting Satnet,
    Beam or Spectrum Assignment and return the maximum valid period."*

Three things that sentence asks for, and all three are what makes this module worth having
rather than three inline comparisons:

* the **intersection**, not three separate yes/no answers;
* the **limiting parent** by name — an operator told "outside the permitted period" with three
  candidate causes has to go and look at all three;
* the **maximum valid period**, so the interface can offer it rather than making somebody
  binary-search for a date that will be accepted.

**Draft and operational are different verdicts on the same facts.** A draft outside its
parents' periods is a warning; the same record entering an active state is a refusal. That is
the answer's own distinction, and it is a single flag here rather than two code paths, because
two code paths would eventually disagree about what "contained" means.

**Temporal containment is not sufficient.** The answer is explicit: the assignment must also
belong to the same Beam and be compatible with the Path's direction, polarization and Payload
Path. Those checks live here too — a module called "containment" that silently let a Path point
at another Beam's assignment would be the worst possible place to put half a rule.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from beams.models import Beam, BeamSpectrumAssignment
from calculations.periods import TimePeriod, intersect
from satnets.models import Satnet


class Limiter:
    """Which parent bounded the period. Values are stable — they reach the interface."""

    SATNET = "SATNET"
    BEAM = "BEAM"
    ASSIGNMENT = "ASSIGNMENT"


@dataclasses.dataclass(frozen=True)
class Finding:
    """One containment problem, with the parent that caused it."""

    code: str
    message: str
    limiter: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "limiter": self.limiter}


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What the requested period is allowed to be, and what is wrong with it.

    ``permitted`` is ``None`` when the three parents share no common period at all — which is a
    different problem from a Path that merely reaches too far, and needs a different message:
    there is no period that would be accepted, so offering a maximum would be a lie.
    """

    permitted: TimePeriod | None
    findings: list[Finding]
    limiter: str = ""

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def blocks_activation(self) -> bool:
        """Every containment finding blocks an operational Path. **OQ-32**.

        Named rather than aliased to ``not ok`` because the two are the same today and will not
        be if a later slice adds an advisory finding here — and the caller that matters is the
        one deciding whether a record may enter an active state.
        """
        return bool(self.findings)


def evaluate(
    *,
    satnet: Satnet,
    beam: Beam,
    assignment: BeamSpectrumAssignment,
    requested: TimePeriod,
    direction: str,
    polarization: str,
    operational: bool,
) -> Verdict:
    """Check a requested period against all three parents, and the assignment's compatibility.

    ``operational`` decides the *severity*, never the *rules*: the same facts are evaluated
    either way, and the caller decides what a finding means. A draft may be saved with findings
    attached; an operational Path may not.
    """
    findings: list[Finding] = []

    findings += _check_assignment_belongs(satnet, beam, assignment, direction, polarization)

    permitted = intersect([satnet.validity, beam.validity, assignment.validity])
    if permitted is None:
        findings.append(
            Finding(
                code="NO_COMMON_PERIOD",
                message=(
                    f"Satnet {satnet.code}, Beam {beam.code} and the spectrum assignment share "
                    f"no period in which all three are valid, so no Satnet Path can be created "
                    f"here. One of the three has to be extended."
                ),
                limiter=_earliest_ending(satnet, beam, assignment),
            )
        )
        return Verdict(permitted=None, findings=findings)

    if not permitted.contains(requested):
        limiter = _limiting_parent(satnet, beam, assignment, requested)
        findings.append(
            Finding(
                code="PERIOD_NOT_CONTAINED",
                message=_describe(requested, permitted, limiter, satnet, beam),
                limiter=limiter,
            )
        )
        return Verdict(permitted=permitted, findings=findings, limiter=limiter)

    return Verdict(permitted=permitted, findings=findings)


def _check_assignment_belongs(
    satnet: Satnet,
    beam: Beam,
    assignment: BeamSpectrumAssignment,
    direction: str,
    polarization: str,
) -> list[Finding]:
    """*"Temporal containment alone is not sufficient."* **OQ-32**.

    The assignment must be the Beam's own, on the Path's direction, and carry a window whose
    polarization matches. Without this a Path could sit neatly inside a period belonging to
    another Beam's entitlement — every date correct, and the spectrum somebody else's.
    """
    findings: list[Finding] = []
    config = assignment.direction_config

    if config.beam_id != beam.pk:
        findings.append(
            Finding(
                code="ASSIGNMENT_WRONG_BEAM",
                message=(
                    f"The spectrum assignment belongs to Beam {config.beam.code}, not to "
                    f"{beam.code}. A Satnet Path may only use its own Beam's entitlement."
                ),
                limiter=Limiter.ASSIGNMENT,
            )
        )
    if beam.pk != satnet.beam_id:
        findings.append(
            Finding(
                code="BEAM_NOT_THE_SATNETS",
                message=(
                    f"Beam {beam.code} is not Satnet {satnet.code}'s Beam ({satnet.beam.code})."
                ),
                limiter=Limiter.BEAM,
            )
        )
    if config.direction != direction:
        findings.append(
            Finding(
                code="ASSIGNMENT_WRONG_DIRECTION",
                message=(
                    f"The spectrum assignment is on the {config.direction} chain, but this "
                    f"Satnet Path is {direction}."
                ),
                limiter=Limiter.ASSIGNMENT,
            )
        )

    window = assignment.frequency_window
    if polarization and window.polarization != polarization:
        findings.append(
            Finding(
                code="ASSIGNMENT_WRONG_POLARIZATION",
                message=(
                    f"The assignment's window {window.code} is {window.polarization}, but this "
                    f"Satnet Path is {polarization}. Two polarizations on one leg are two "
                    f"windows (§25), so they are two assignments."
                ),
                limiter=Limiter.ASSIGNMENT,
            )
        )
    if config.payload_path_id != assignment.payload_path_id:
        findings.append(
            Finding(
                code="ASSIGNMENT_STALE_PAYLOAD_PATH",
                message=(
                    "The spectrum assignment was drawn against a payload path this direction "
                    "no longer uses. A new assignment is required when the payload "
                    "configuration changes."
                ),
                limiter=Limiter.ASSIGNMENT,
            )
        )
    return findings


def _limiting_parent(
    satnet: Satnet,
    beam: Beam,
    assignment: BeamSpectrumAssignment,
    requested: TimePeriod,
) -> str:
    """Which parent the requested period actually broke.

    Checked in the order an operator can act on: a Satnet's dates are theirs to change, a
    Beam's belong to engineering, and an assignment's to the payload plan. When more than one
    is breached, naming the nearest one first is what makes the message actionable — fixing it
    may be enough, and if it is not the next attempt names the next.
    """
    for label, period in (
        (Limiter.SATNET, satnet.validity),
        (Limiter.BEAM, beam.validity),
        (Limiter.ASSIGNMENT, assignment.validity),
    ):
        if not period.contains(requested):
            return label
    return ""


def _earliest_ending(satnet: Satnet, beam: Beam, assignment: BeamSpectrumAssignment) -> str:
    """Which parent closes first, for the no-common-period case."""
    candidates = [
        (Limiter.SATNET, satnet.validity),
        (Limiter.BEAM, beam.validity),
        (Limiter.ASSIGNMENT, assignment.validity),
    ]
    bounded = [(label, period.end) for label, period in candidates if period.end is not None]
    if not bounded:
        # All three open-ended, so the overlap failed on the *starts*: the one starting last is
        # what stops them ever being valid together.
        return max(candidates, key=lambda item: item[1].start)[0]
    return min(bounded, key=lambda item: item[1])[0]


def _describe(
    requested: TimePeriod,
    permitted: TimePeriod,
    limiter: str,
    satnet: Satnet,
    beam: Beam,
) -> str:
    """The message §9.5 asks for: what is wrong, what caused it, and what would be accepted."""
    names = {
        Limiter.SATNET: f"Satnet {satnet.code}",
        Limiter.BEAM: f"Beam {beam.code}",
        Limiter.ASSIGNMENT: "the spectrum assignment",
    }
    cause = names.get(limiter, "one of its parents")
    return (
        f"The requested period {_format(requested)} is not contained within {cause}. "
        f"The maximum permitted period here is {_format(permitted)}."
    )


def _format(period: TimePeriod) -> str:
    end = "open-ended" if period.end is None else _date(period.end)
    return f"{_date(period.start)} to {end}"


def _date(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")
