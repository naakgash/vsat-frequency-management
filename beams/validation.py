"""Beam configuration rules. Specification sections 5.2, 5.3, 5.4 and 26.6.

§26.6 is the requirement this module exists for: *a Beam cannot be activated while its
mandatory FWD/RTN configuration is invalid*. Everything here answers one question — is this
Beam safe to turn on — and answers it with reasons rather than a boolean.

The rules are deliberately **not** the calculation engine's. ``calculations.validation``
checks an individual placement's arithmetic; this checks whether a Beam's master data hangs
together at all. A Beam can be perfectly valid and still have no room for a particular
transmission, and the two failures need different words.
"""

from __future__ import annotations

import dataclasses

from beams.constants import ConfigurationState, ValidationOutcome
from beams.models import Beam, BeamDirectionConfig
from calculations.validation import Severity
from inventory.models import PayloadPath, PayloadPolarizationMapping


@dataclasses.dataclass(frozen=True)
class Finding:
    """One rule result, against one direction or against the Beam as a whole."""

    code: str
    severity: Severity
    message: str
    direction: str = ""
    reference: str = ""

    @property
    def blocks(self) -> bool:
        return self.severity is Severity.ERROR

    def as_dict(self) -> dict[str, str]:
        """For ``BeamValidationResult.findings``.

        Stored as plain JSON rather than pickled objects so a result written today is still
        readable after this dataclass changes shape.
        """
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "direction": self.direction,
            "reference": self.reference,
        }


@dataclasses.dataclass(frozen=True)
class Report:
    """Everything one validation run concluded."""

    findings: list[Finding]
    state: ConfigurationState

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocks]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocks]

    @property
    def is_activatable(self) -> bool:
        return self.state is ConfigurationState.VALID

    @property
    def outcome(self) -> ValidationOutcome:
        if self.blocking or self.state is not ConfigurationState.VALID:
            return ValidationOutcome.FAILED
        if self.warnings:
            return ValidationOutcome.PASSED_WITH_WARNINGS
        return ValidationOutcome.PASSED


def validate(beam: Beam) -> Report:
    """Every rule that applies to a Beam, in one pass.

    Returns findings rather than raising, for the reason given in
    ``calculations.validation``: the builder has to show an administrator everything wrong
    with a configuration on one screen.
    """
    findings: list[Finding] = []
    configs = list(beam.direction_configs.all())

    findings += _check_beam_shape(beam, configs)
    enabled = [c for c in configs if c.is_enabled]
    for config in enabled:
        findings += _check_direction(config)

    return Report(findings=findings, state=_state_for(configs, enabled, findings))


def _state_for(
    configs: list[BeamDirectionConfig],
    enabled: list[BeamDirectionConfig],
    findings: list[Finding],
) -> ConfigurationState:
    """Incomplete, invalid, or valid — in that order of precedence.

    Incompleteness outranks invalidity deliberately. A half-configured direction will
    produce rule failures that are simply consequences of the missing data, and reporting
    those as "invalid" sends someone hunting for a problem that is really just unfinished
    work.
    """
    if not configs or not enabled:
        return ConfigurationState.INCOMPLETE
    if any(not config.is_configured for config in enabled):
        return ConfigurationState.INCOMPLETE
    if any(f.blocks for f in findings):
        return ConfigurationState.INVALID
    return ConfigurationState.VALID


# ---------------------------------------------------------------------------
# Beam-level rules
# ---------------------------------------------------------------------------
def _check_beam_shape(beam: Beam, configs: list[BeamDirectionConfig]) -> list[Finding]:
    findings: list[Finding] = []

    if not any(c.is_enabled for c in configs):
        findings.append(
            Finding(
                code="NO_ENABLED_DIRECTION",
                severity=Severity.ERROR,
                message=(
                    "Every direction is disabled, so this Beam carries no traffic in either "
                    "direction. Enable FWD, RTN, or both."
                ),
                reference="section 5.4",
            )
        )

    for config in configs:
        path = config.payload_path
        if path is not None and path.satellite_id != beam.satellite_id:
            findings.append(
                Finding(
                    code="PAYLOAD_PATH_WRONG_SATELLITE",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"The payload path belongs to satellite "
                        f"{path.satellite.code}, but this Beam is on "
                        f"{beam.satellite.code}. A Beam cannot translate through another "
                        f"satellite's payload."
                    ),
                    reference="section 5.2",
                )
            )

    disabled = [c.direction for c in configs if not c.is_enabled]
    if disabled:
        # Not a problem — §5.4 makes it a deliberate business case — but it must be visible.
        # A receive-only Beam that nobody realises is receive-only is a support call.
        findings.append(
            Finding(
                code="DIRECTION_DISABLED",
                severity=Severity.WARNING,
                message=(
                    f"{', '.join(sorted(disabled))} is explicitly disabled and will carry no "
                    f"traffic. This is a valid configuration, shown so it is not a surprise."
                ),
                reference="section 5.4",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Direction-level rules
# ---------------------------------------------------------------------------
def _check_direction(config: BeamDirectionConfig) -> list[Finding]:
    if not config.is_configured:
        return [
            Finding(
                code="DIRECTION_INCOMPLETE",
                severity=Severity.ERROR,
                direction=config.direction,
                message=(
                    "This direction is enabled but has no payload path and windows. Complete "
                    "it or disable it explicitly."
                ),
                reference="sections 5.2, 5.3",
            )
        ]

    findings: list[Finding] = []
    findings += _check_window_identity(config)
    findings += _check_direction_sides(config)
    findings += _check_polarizations(config)
    findings += _check_canonical_leg(config)
    findings += _check_equipment(config)
    findings += _check_spectrum_resources(config)
    findings += _check_spectrum_assignments(config)
    return findings


def _check_window_identity(config: BeamDirectionConfig) -> list[Finding]:
    """**A-06**: the Beam's windows must be *identical* to the Payload Path's.

    This half of A-06 survived the OQ-27 answer unchanged. What changed is what the windows
    *mean*: they are the maximum payload capability, and the spectrum a Beam may actually use
    is its :class:`~beams.models.BeamSpectrumAssignment` rows (ADR-0019). So the Beam still
    points at the path's windows exactly — narrowing happens one level down, where it can
    carry its own effective period.
    """
    path = _path_of(config)
    # Guaranteed present by is_configured, alongside the path — named locally so the
    # messages below stay readable.
    uplink, downlink = config.uplink_window, config.downlink_window
    assert uplink is not None and downlink is not None
    findings = []

    if config.uplink_window_id != path.uplink_window_id:
        findings.append(
            Finding(
                code="UPLINK_WINDOW_NOT_PATH_WINDOW",
                severity=Severity.ERROR,
                direction=config.direction,
                message=(
                    f"The uplink window ({uplink.code}) is not the payload "
                    f"path's uplink window ({path.uplink_window.code}). A Beam uses its "
                    f"payload path's windows exactly; a sub-range is expressed as a "
                    f"spectrum assignment, not by pointing at a different window."
                ),
                reference="A-06, ADR-0019",
            )
        )
    if config.downlink_window_id != path.downlink_window_id:
        findings.append(
            Finding(
                code="DOWNLINK_WINDOW_NOT_PATH_WINDOW",
                severity=Severity.ERROR,
                direction=config.direction,
                message=(
                    f"The downlink window ({downlink.code}) is not the payload "
                    f"path's downlink window ({path.downlink_window.code})."
                ),
                reference="A-06, ADR-0019",
            )
        )
    return findings


def _check_direction_sides(config: BeamDirectionConfig) -> list[Finding]:
    """The Payload Path's direction must be this direction's. Specification section 5.2."""
    path = _path_of(config)
    if path.direction == config.direction:
        return []
    return [
        Finding(
            code="PAYLOAD_PATH_WRONG_DIRECTION",
            severity=Severity.ERROR,
            direction=config.direction,
            message=(
                f"This is the {config.direction} chain, but the payload path is "
                f"{path.direction}. A forward chain runs hub uplink to remote "
                f"downlink; a return chain runs the other way."
            ),
            reference="section 5.2, A-03",
        )
    ]


def _check_polarizations(config: BeamDirectionConfig) -> list[Finding]:
    """The chosen pair must be one the Payload Path permits. Specification section 13.7.

    When the path lists **no** mappings this warns rather than blocks. Which pairs are
    permitted is **OQ-03** and nothing is seeded, so treating an empty list as "nothing is
    allowed" would make every Beam un-activatable until an open question is answered — which
    is a worse failure than proceeding with the gap recorded.
    """
    if not (config.uplink_polarization and config.downlink_polarization):
        return [
            Finding(
                code="POLARIZATION_NOT_SET",
                severity=Severity.ERROR,
                direction=config.direction,
                message="Both the uplink and downlink polarization must be chosen.",
                reference="sections 5.2, 5.3",
            )
        ]

    path = _path_of(config)
    permitted = list(
        PayloadPolarizationMapping.objects.filter(payload_path=path).values_list(
            "uplink_polarization", "downlink_polarization"
        )
    )
    if not permitted:
        return [
            Finding(
                code="NO_POLARIZATION_MAPPINGS",
                severity=Severity.WARNING,
                direction=config.direction,
                message=(
                    f"Payload path {path.code} lists no permitted polarization "
                    f"pairs, so this pair cannot be checked. Which pairs are allowed is an open "
                    f"question (OQ-03) and none are supplied by the platform."
                ),
                reference="section 13.7, OQ-03",
            )
        ]

    pair = (config.uplink_polarization, config.downlink_polarization)
    if pair in permitted:
        return []
    return [
        Finding(
            code="POLARIZATION_NOT_PERMITTED",
            severity=Severity.ERROR,
            direction=config.direction,
            message=(
                f"{pair[0]} up / {pair[1]} down is not one of the pairs payload path "
                f"{path.code} permits."
            ),
            reference="section 13.7",
        )
    ]


def _check_canonical_leg(config: BeamDirectionConfig) -> list[Finding]:
    """The canonical leg must belong to this direction's own chain (**A-07**)."""
    if not config.canonical_leg:
        return [
            Finding(
                code="CANONICAL_LEG_NOT_SET",
                severity=Severity.ERROR,
                direction=config.direction,
                message=(
                    "No canonical leg is set, so the builder cannot say which centre "
                    "frequency the operator enters."
                ),
                reference="A-07, section 9.3",
            )
        ]

    path = _path_of(config)
    legs = {path.uplink_window_side, path.downlink_window_side}
    if config.canonical_leg in legs:
        return []
    return [
        Finding(
            code="CANONICAL_LEG_NOT_IN_CHAIN",
            severity=Severity.ERROR,
            direction=config.direction,
            message=(
                f"The canonical leg {config.canonical_leg} is not part of this chain, which "
                f"runs {' to '.join(sorted(legs))}. The operator would be entering a centre "
                f"frequency for a leg this direction does not use."
            ),
            reference="A-07",
        )
    ]


def _check_equipment(config: BeamDirectionConfig) -> list[Finding]:
    """The candidate equipment pool. Specification sections 5.2, 5.3, 13.5.

    An empty pool warns rather than blocks. Equipment limits are **OQ-04** and no profile is
    seeded, so a Beam with no candidates is the expected state until RF engineering supplies
    them — and a Satnet Path will refuse loudly in S11 when it finds nothing to convert
    through.
    """
    profiles = list(config.equipment_profiles.select_related("equipment_profile"))

    if not profiles:
        return [
            Finding(
                code="NO_EQUIPMENT_PROFILES",
                severity=Severity.WARNING,
                direction=config.direction,
                message=(
                    "No equipment profiles are listed for this direction, so a Satnet Path "
                    "will have nothing to convert through. Profile limits are an open "
                    "question (OQ-04) and none are supplied by the platform."
                ),
                reference="sections 5.2, 13.5, OQ-04",
            )
        ]

    findings = []
    for entry in profiles:
        profile = entry.equipment_profile
        if profile.band_id != config.beam.band_id:
            findings.append(
                Finding(
                    code="EQUIPMENT_WRONG_BAND",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"Profile {profile.code} is a {profile.band.code} profile, but this "
                        f"Beam is on {config.beam.band.code}."
                    ),
                    reference="section 13.5",
                )
            )
        elif not profile.is_active:
            findings.append(
                Finding(
                    code="EQUIPMENT_INACTIVE",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"Profile {profile.code} is deactivated and cannot be used by a new "
                        f"allocation."
                    ),
                    reference="section 20",
                )
            )

    return findings


def _check_spectrum_resources(config: BeamDirectionConfig) -> list[Finding]:
    """Both of this direction's legs must map to a Spectrum Resource. **OQ-25**, ADR-0018.

    This is the rule that replaced *"the Beam is the pool"*. Overlap is judged on the
    resource an allocation occupies, so a leg mapped to nothing competes with nothing —
    every allocation on it would be accepted, including one that genuinely collides.

    It **blocks**, and the severity is the whole point. A missing mapping is invisible at
    allocation time: there is no error, no conflict and no gap in the data, just a permission
    to interfere. The database cannot detect it either — an exclusion constraint can only
    compare rows that exist. So it is caught here, before the Beam can be activated at all.

    There is deliberately no fallback. Inferring one resource per Beam would reinstate the
    superseded **A-01** under a new name; inferring one per satellite would forbid all reuse.
    Both are guesses about interference, and a guess is what this record exists to replace.
    """
    path = _path_of(config)
    mapped = {link.spectrum_resource.leg for link in config.spectrum_resources.all()}
    findings: list[Finding] = []

    for leg, window in (
        (path.uplink_window_side, config.uplink_window),
        (path.downlink_window_side, config.downlink_window),
    ):
        if leg in mapped:
            continue
        assert window is not None
        findings.append(
            Finding(
                code="LEG_HAS_NO_SPECTRUM_RESOURCE",
                severity=Severity.ERROR,
                direction=config.direction,
                message=(
                    f"The {leg} leg ({window.code}) is not mapped to any spectrum resource, "
                    f"so nothing would compete with an allocation on it. Add the resource "
                    f"this leg shares — the payload input or RF chain from the approved plan."
                ),
                reference="OQ-25, ADR-0018",
            )
        )

    for link in config.spectrum_resources.all():
        resource = link.spectrum_resource
        if resource.satellite_id != config.beam.satellite_id:
            findings.append(
                Finding(
                    code="SPECTRUM_RESOURCE_WRONG_SATELLITE",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"Spectrum resource {resource.code} belongs to satellite "
                        f"{resource.satellite.code}, not to this Beam's "
                        f"{config.beam.satellite.code}."
                    ),
                    reference="ADR-0018",
                )
            )
        elif not resource.is_active:
            findings.append(
                Finding(
                    code="SPECTRUM_RESOURCE_DEACTIVATED",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"Spectrum resource {resource.code} has been deactivated. An "
                        f"allocation cannot be judged against a resource that no longer "
                        f"describes the payload."
                    ),
                    reference="A-22",
                )
            )

    return findings


def _check_spectrum_assignments(config: BeamDirectionConfig) -> list[Finding]:
    """Each window needs at least one active assignment. **OQ-27**, ADR-0019.

    The Frequency Window is the maximum payload capability; the assignments are what the Beam
    may actually use. A direction whose window has no active assignment can allocate nothing,
    which is correct and would be baffling as an empty gap list — so it is reported here with
    the reason.

    Containment inside the window is *not* checked: ``ck_assignment_within_window`` makes it
    impossible to store an assignment that escapes its window, and re-checking a database
    guarantee in the service layer teaches a reader to distrust the constraint.
    """
    assignments = list(config.spectrum_assignments.all())
    by_window: dict[str, list[object]] = {}
    for assignment in assignments:
        if assignment.is_active:
            by_window.setdefault(str(assignment.frequency_window_id), []).append(assignment)

    findings: list[Finding] = []
    for window in (config.uplink_window, config.downlink_window):
        assert window is not None
        if str(window.pk) not in by_window:
            findings.append(
                Finding(
                    code="WINDOW_HAS_NO_ACTIVE_ASSIGNMENT",
                    severity=Severity.ERROR,
                    direction=config.direction,
                    message=(
                        f"Window {window.code} has no active spectrum assignment, so this "
                        f"direction may use none of it. Assign the whole window, or the "
                        f"sub-ranges this Beam is entitled to."
                    ),
                    reference="OQ-27, ADR-0019",
                )
            )
    return findings


def _path_of(config: BeamDirectionConfig) -> PayloadPath:
    """The direction's payload path, narrowed from optional.

    Every caller runs only after :attr:`BeamDirectionConfig.is_configured`, which is exactly
    the check that the three references are present — but that guarantee lives in a property
    and mypy cannot follow it across the call. Asserting here states the precondition once
    rather than repeating a null check in five rules that can never see a null.
    """
    assert config.payload_path is not None, "checked by BeamDirectionConfig.is_configured"
    return config.payload_path
