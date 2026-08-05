"""Creating a Satnet Path. §9.5, §15.6, ADR-0013.

The whole allocation — the Path row and every occupancy row that holds its spectrum — is
written in **one transaction**. A half-written allocation is worse than a refused one: rows
that committed would hold spectrum for a Path that does not exist, and nothing would release
them.

**The server repeats every check on save** (§9.5). The wizard's live preview is a courtesy; a
form submitted an hour after the preview was rendered is being validated against master data
that may have been superseded since, and against reservations that certainly have changed.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction

from accounts import policy
from accounts.models import User
from accounts.types import Actor
from audit import services as audit_services
from beams.models import BeamDirectionConfig, BeamSpectrumAssignment
from calculations import bandwidth, guards
from calculations.periods import TimePeriod
from calculations.ranges import FrequencyRange
from calculations.translation import TranslationMethod, TranslationSpec
from calculations.types import BandwidthRequest, GuardMode, GuardPolicySpec, GuardSource
from satnet_paths.constants import (
    MANAGE_SATNET_PATHS,
    PATH_BLOCKED,
    PATH_CREATED,
    PATH_UPDATED,
    InputMode,
    PathStatus,
)
from satnet_paths.models import SatnetPath
from satnets import containment
from satnets import scope as satnet_scope
from satnets.models import Satnet
from spectrum import selectors as spectrum_selectors
from spectrum import services as spectrum_services


class PathBlockedError(Exception):
    """The allocation was refused, with everything §9.5 requires the message to contain.

    Carries structured findings rather than one string. §9.5 asks the blocking message to name
    the rule, the Beam, the Window, the proposed range, the conflicting Satnet Path, the
    overlap amount, the validity overlap and the suggested gaps — that is a screen, not a
    sentence, and flattening it early throws away everything the screen needs.
    """

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        super().__init__("; ".join(finding.message for finding in findings))


@dataclasses.dataclass(frozen=True)
class Conflict:
    """One competing allocation, and how much of it is in the way. §9.5."""

    resource_code: str
    leg: str
    conflicting_path_code: str
    conflicting_range: tuple[int, int]
    overlap_hz: int
    validity_overlap: tuple[datetime, datetime | None]


@dataclasses.dataclass(frozen=True)
class Finding:
    """One reason the allocation was refused, with the context §9.5 asks for."""

    code: str
    message: str
    rule: str = ""
    beam_code: str = ""
    window_code: str = ""
    proposed: tuple[int, int] | None = None
    conflicts: tuple[Conflict, ...] = ()
    suggested_gaps: tuple[tuple[int, int], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "rule": self.rule,
            "beam": self.beam_code,
            "window": self.window_code,
            "proposed": list(self.proposed) if self.proposed else None,
            "conflicts": [dataclasses.asdict(conflict) for conflict in self.conflicts],
            "suggested_gaps": [list(gap) for gap in self.suggested_gaps],
        }


@dataclasses.dataclass(frozen=True)
class Proposal:
    """A fully computed allocation that has not been saved. §9.3.

    Auto-place and the live preview both produce one of these. It is deliberately a value
    rather than an unsaved model instance: §9.3 says Auto-place *proposes and never saves*, and
    an unsaved instance is one ``.save()`` away from doing exactly that.
    """

    placement: Any
    config: BeamDirectionConfig
    canonical_assignment: BeamSpectrumAssignment
    translated_assignment: BeamSpectrumAssignment
    guard_widths: Any
    symbol_rate_sps: int | None
    occupied_bw_hz: int
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return not self.findings


def preview(
    *,
    satnet: Satnet,
    direction: str,
    input_mode: str,
    input_value: int,
    rolloff: Decimal,
    centre_hz: int,
    valid_from: datetime,
    valid_until: datetime | None = None,
    guard_policy: Any = None,
    exclude_path_id: uuid.UUID | None = None,
) -> Proposal:
    """Compute an allocation and everything wrong with it, without saving. §9.3, §9.4.

    The same function backs the wizard's live preview, Auto-place's candidate check, and the
    server-side re-check on save — deliberately one function, because a preview computed by
    different code from the one that saves is a preview of something else.
    """
    config = satnet.beam.direction_configs.get(direction=direction)
    request = BandwidthRequest(
        rolloff=rolloff,
        symbol_rate_sps=input_value if input_mode == InputMode.SYMBOL_RATE else None,
        occupied_bandwidth_hz=input_value if input_mode == InputMode.OCCUPIED_BW else None,
    )
    symbol_rate, occupied_bw = bandwidth.resolve_request(request)
    widths = guards.resolve(_guard_spec(guard_policy, satnet), occupied_bw)

    placement = bandwidth.place_both_sides(
        request=request,
        centre_hz=centre_hz,
        guards=widths,
        translation=_translation_of(config),
        entered_side="UPLINK",
    )

    canonical, translated = _sides_for(config, placement)
    findings: list[Finding] = []
    canonical_assignment = _assignment_for(config, canonical.window)
    translated_assignment = _assignment_for(config, translated.window)

    if canonical_assignment is None or translated_assignment is None:
        findings.append(
            Finding(
                code="NO_ACTIVE_ASSIGNMENT",
                rule="OQ-27 / ADR-0019",
                beam_code=satnet.beam.code,
                message=(
                    "This Beam direction has no active spectrum assignment on one of its legs, "
                    "so it may use none of that window."
                ),
            )
        )
        return Proposal(
            placement=placement,
            config=config,
            canonical_assignment=canonical_assignment,  # type: ignore[arg-type]
            translated_assignment=translated_assignment,  # type: ignore[arg-type]
            guard_widths=widths,
            symbol_rate_sps=symbol_rate,
            occupied_bw_hz=occupied_bw,
            findings=findings,
        )

    requested_period = TimePeriod(valid_from, valid_until)
    findings += _check_containment(
        satnet, config, canonical_assignment, requested_period, canonical
    )
    findings += _check_conflicts(
        satnet, config, canonical, canonical_assignment, requested_period, exclude_path_id
    )
    findings += _check_conflicts(
        satnet, config, translated, translated_assignment, requested_period, exclude_path_id
    )

    return Proposal(
        placement=placement,
        config=config,
        canonical_assignment=canonical_assignment,
        translated_assignment=translated_assignment,
        guard_widths=widths,
        symbol_rate_sps=symbol_rate,
        occupied_bw_hz=occupied_bw,
        findings=findings,
    )


def auto_place(
    *,
    satnet: Satnet,
    direction: str,
    input_mode: str,
    input_value: int,
    rolloff: Decimal,
    valid_from: datetime,
    valid_until: datetime | None = None,
    guard_policy: Any = None,
) -> Proposal | None:
    """Propose the lowest centre frequency that fits. §9.3 — **proposes and never saves**.

    Returns a :class:`Proposal`, not a saved row and not an unsaved model instance. The
    operator still has to accept it, and the server re-checks it on save, because the gap it
    was placed in can be taken between the proposal and the click.
    """
    config = satnet.beam.direction_configs.get(direction=direction)
    request = BandwidthRequest(
        rolloff=rolloff,
        symbol_rate_sps=input_value if input_mode == InputMode.SYMBOL_RATE else None,
        occupied_bandwidth_hz=input_value if input_mode == InputMode.OCCUPIED_BW else None,
    )
    _, occupied_bw = bandwidth.resolve_request(request)
    widths = guards.resolve(_guard_spec(guard_policy, satnet), occupied_bw)
    needed = occupied_bw + widths.left_hz + widths.right_hz

    leg = _canonical_leg(config)
    summary = spectrum_selectors.capacity(config, leg=leg)
    gap = next((candidate for candidate in summary.gaps if candidate.fits(needed)), None)
    if gap is None:
        return None

    # Lowest edge of the gap plus the left guard plus half the occupied width: the leftmost
    # centre whose *allocated* range still fits. Deterministic by construction (§9.3), so
    # reopening the wizard shows the same answer.
    centre = gap.range.start_hz + widths.left_hz + occupied_bw // 2
    return preview(
        satnet=satnet,
        direction=direction,
        input_mode=input_mode,
        input_value=input_value,
        rolloff=rolloff,
        centre_hz=centre,
        valid_from=valid_from,
        valid_until=valid_until,
        guard_policy=guard_policy,
    )


def create(
    *,
    actor: Actor,
    satnet: Satnet,
    values: dict[str, Any],
    reason: str = "",
) -> SatnetPath:
    """Create a Satnet Path and every reservation that holds its spectrum. §9.5, §15.6.

    Authorise, then re-check, then transact. The re-check is not a formality: the preview an
    operator accepted was computed against reservations that have since changed, and against
    master data that may have been superseded.
    """
    policy.require(actor, MANAGE_SATNET_PATHS, satnet, reason=reason)
    allowed, why = satnet_scope.may_act_on(actor, beam_id=satnet.beam_id, hub_id=satnet.hub_id)
    if not allowed:
        policy.record_denial(actor, MANAGE_SATNET_PATHS, satnet, detail=f"out of scope: {why}")
        raise PathBlockedError([Finding(code="OUT_OF_SCOPE", message=why, rule="A-17")])

    proposal = preview(
        satnet=satnet,
        direction=values["direction"],
        input_mode=values["input_mode"],
        input_value=values["input_value"],
        rolloff=values["rolloff"],
        centre_hz=values["canonical_center_hz"],
        valid_from=values["valid_from"],
        valid_until=values.get("valid_until"),
        guard_policy=values.get("guard_policy"),
    )
    status = values.get("status", PathStatus.DRAFT)
    operational = status != PathStatus.DRAFT

    if proposal.findings and operational:
        audit_services.record(
            action=PATH_BLOCKED,
            actor=actor,
            obj=satnet,
            after={"findings": [f.as_dict() for f in proposal.findings]},
            change_reason=reason,
            message=f"Refused a Satnet Path under {satnet.code}",
        )
        raise PathBlockedError(proposal.findings)

    return _create(actor=actor, satnet=satnet, values=values, proposal=proposal, reason=reason)


def create_revision(
    *,
    actor: Actor,
    predecessor: SatnetPath,
    values: dict[str, Any],
    reason: str = "",
) -> SatnetPath:
    """Write the successor half of a revision. §15.4, ADR-0014.

    Called only by ``lifecycle.revise``, which has already closed the predecessor's period and
    released its spectrum — an ordering this function depends on and cannot check, because a
    predecessor still holding its own frequency would refuse its own replacement.

    The successor is **recomputed, not copied**. Only the operator's inputs carry forward; the
    edges, bandwidths and IF are derived again from whatever master data is current, which is
    the point of revising rather than editing.
    """
    proposal = preview(
        satnet=predecessor.satnet,
        direction=values["direction"],
        input_mode=values["input_mode"],
        input_value=values["input_value"],
        rolloff=values["rolloff"],
        centre_hz=values["canonical_center_hz"],
        valid_from=values["valid_from"],
        valid_until=values.get("valid_until"),
        guard_policy=values.get("guard_policy"),
        exclude_path_id=predecessor.pk,
    )
    if proposal.findings:
        raise PathBlockedError(proposal.findings)

    return _create(
        actor=actor,
        satnet=predecessor.satnet,
        values=values,
        proposal=proposal,
        reason=reason,
        predecessor=predecessor,
    )


@transaction.atomic
def rewrite(
    *,
    actor: Actor,
    path: SatnetPath,
    values: dict[str, Any],
    reason: str = "",
) -> SatnetPath:
    """Change an editable allocation in place, recomputing everything derived from it.

    Authorisation and the §15.4 status rule are ``lifecycle.edit``'s; this is the write.

    The old occupancy rows are released **before** the new ones are written, inside the one
    transaction (**A-14**). Nearly every edit keeps some of its own frequency, so an allocation
    that did not release first would be refused by the rows it is replacing — the same ordering
    revision needs, for the same reason.
    """
    proposal = preview(
        satnet=path.satnet,
        direction=values["direction"],
        input_mode=values["input_mode"],
        input_value=values["input_value"],
        rolloff=values["rolloff"],
        centre_hz=values["canonical_center_hz"],
        valid_from=values["valid_from"],
        valid_until=values.get("valid_until"),
        guard_policy=values.get("guard_policy"),
        exclude_path_id=path.pk,
    )
    if proposal.findings and path.is_operational:
        raise PathBlockedError(proposal.findings)

    before = audit_services.snapshot(path)
    spectrum_services.release(actor=actor, satnet_path_id=str(path.pk), reason=reason)

    canonical, translated = _sides_for(proposal.config, proposal.placement)
    for field, value in _derived_fields(values, proposal, canonical, translated).items():
        setattr(path, field, value)
    path.status = values.get("status", path.status)
    path.change_reason = reason or path.change_reason
    path.record_version += 1
    path.updated_by = _acting_user(actor)
    path.full_clean(exclude=["created_by", "updated_by", "supersedes"])
    path.save()

    if path.is_operational:
        spectrum_services.reserve(
            actor=actor,
            occupancies=occupancies_of(path, config=proposal.config),
            satnet_path_id=str(path.pk),
            direction=path.direction,
            status=path.status,
            valid_from=path.valid_from,
            valid_until=path.valid_until,
            reason=reason,
        )

    audit_services.record(
        action=PATH_UPDATED,
        actor=actor,
        obj=path,
        before=before,
        after=audit_services.snapshot(path),
        change_reason=reason,
        message=f"Changed Satnet Path {path.code}",
    )
    return path


def occupancies_of(
    path: SatnetPath, *, config: BeamDirectionConfig | None = None
) -> list[spectrum_services.Occupancy]:
    """This Path's occupancies, rebuilt from what it stored. **A-23**.

    Read back from the record rather than recomputed: the Payload Path behind it is versioned
    and may have been superseded since the allocation was validated, and recomputing would
    reserve spectrum nobody asked for. The stored edges *are* the allocation
    (`docs/design/02` §4.2).
    """
    config = config or path.beam.direction_configs.get(direction=path.direction)
    return [
        spectrum_services.Occupancy(
            assignment=assignment,
            leg=leg,
            polarization=polarization,
            occupied=FrequencyRange(occupied_start, occupied_end),
            allocated=FrequencyRange(allocated_start, allocated_end),
            resource_ids=tuple(
                str(link.spectrum_resource_id)
                for link in config.spectrum_resources.all()
                if link.spectrum_resource.leg == leg
            ),
        )
        for assignment, leg, polarization, (
            occupied_start,
            occupied_end,
        ), (allocated_start, allocated_end) in (
            (
                path.canonical_assignment,
                path.canonical_leg,
                path.canonical_polarization,
                (path.canonical_occupied_start_hz, path.canonical_occupied_end_hz),
                (path.canonical_allocated_start_hz, path.canonical_allocated_end_hz),
            ),
            (
                path.translated_assignment,
                path.translated_leg,
                path.translated_polarization,
                (path.translated_occupied_start_hz, path.translated_occupied_end_hz),
                (path.translated_allocated_start_hz, path.translated_allocated_end_hz),
            ),
        )
    ]


def _derived_fields(
    values: dict[str, Any], proposal: Proposal, canonical: _Side, translated: _Side
) -> dict[str, Any]:
    """Everything the engine owns, in one place. §26.16.

    Shared by creation, revision and edit so the three cannot drift — a field written on
    creation but forgotten on edit would leave a record whose stored arithmetic describes an
    allocation it no longer is.
    """
    return {
        "code": values["code"],
        "direction": values["direction"],
        "valid_from": values["valid_from"],
        "valid_until": values.get("valid_until"),
        "input_mode": values["input_mode"],
        "input_value": values["input_value"],
        "rolloff": values["rolloff"],
        "guard_policy": values.get("guard_policy"),
        "guard_left_hz": proposal.guard_widths.left_hz,
        "guard_right_hz": proposal.guard_widths.right_hz,
        "symbol_rate_sps": proposal.symbol_rate_sps,
        "occupied_bw_hz": proposal.occupied_bw_hz,
        "allocated_bw_hz": canonical.placement.allocated.width_hz,
        "canonical_leg": canonical.leg,
        "canonical_window": canonical.window,
        "canonical_assignment": proposal.canonical_assignment,
        "canonical_center_hz": values["canonical_center_hz"],
        "canonical_occupied_start_hz": canonical.placement.occupied.start_hz,
        "canonical_occupied_end_hz": canonical.placement.occupied.end_hz,
        "canonical_allocated_start_hz": canonical.placement.allocated.start_hz,
        "canonical_allocated_end_hz": canonical.placement.allocated.end_hz,
        "canonical_polarization": canonical.window.polarization,
        "translated_leg": translated.leg,
        "translated_window": translated.window,
        "translated_assignment": proposal.translated_assignment,
        "translated_center_hz": _centre_of(translated.placement.occupied),
        "translated_occupied_start_hz": translated.placement.occupied.start_hz,
        "translated_occupied_end_hz": translated.placement.occupied.end_hz,
        "translated_allocated_start_hz": translated.placement.allocated.start_hz,
        "translated_allocated_end_hz": translated.placement.allocated.end_hz,
        "translated_polarization": translated.window.polarization,
        "gateway": values.get("gateway"),
        "decimator_assignment": values.get("decimator_assignment"),
    }


@transaction.atomic
def _create(
    *,
    actor: Actor,
    satnet: Satnet,
    values: dict[str, Any],
    proposal: Proposal,
    reason: str,
    predecessor: SatnetPath | None = None,
) -> SatnetPath:
    canonical, translated = _sides_for(proposal.config, proposal.placement)
    status = values.get("status", PathStatus.DRAFT)

    path = SatnetPath(
        satnet=satnet,
        # Derived, never bound (§26.16). `_derived_fields` is everything the engine owns, and
        # the form's field list contains none of it — the same dictionary is used by an edit
        # and by a revision, so the three writers cannot drift apart.
        beam=satnet.beam,
        status=status,
        change_reason=reason,
        **_derived_fields(values, proposal, canonical, translated),
    )
    if predecessor is not None:
        # §15.4. The group is constant across the chain so the history view is one indexed
        # query, and `ck_path_revision` refuses any later revision that supersedes nothing.
        path.revision_group = predecessor.revision_group
        path.revision_number = predecessor.revision_number + 1
        path.supersedes = predecessor
    # Recorded here rather than left to the audit trail alone, because the second-person
    # approval rule compares against it (**OQ-11**): an approver who is also the author has to
    # be identifiable from the record itself, not from a search through events.
    path.created_by = _acting_user(actor)
    path.updated_by = path.created_by
    path.full_clean(exclude=["created_by", "updated_by", "supersedes"])
    path.save()

    if path.is_operational:
        spectrum_services.reserve(
            actor=actor,
            occupancies=[
                _occupancy(proposal.config, side, assignment)
                for side, assignment in (
                    (canonical, proposal.canonical_assignment),
                    (translated, proposal.translated_assignment),
                )
            ],
            satnet_path_id=str(path.pk),
            direction=path.direction,
            status=status,
            valid_from=path.valid_from,
            valid_until=path.valid_until,
            reason=reason,
        )

    audit_services.record(
        action=PATH_CREATED,
        actor=actor,
        obj=path,
        after=audit_services.snapshot(path),
        change_reason=reason,
        message=f"Created Satnet Path {path.code} under {satnet.code}",
    )
    return path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class _Side:
    leg: str
    window: Any
    placement: Any


def _sides_for(config: BeamDirectionConfig, placement: Any) -> tuple[_Side, _Side]:
    """Which of the engine's two legs is canonical, and which is its image. **A-07**.

    The engine works in uplink/downlink; the record and the reservations work in
    canonical/translated, because which side the operator typed is configuration (**OQ-28**).
    Converting once here means nothing downstream has to know both vocabularies.
    """
    path = config.payload_path
    assert path is not None
    uplink = _Side(path.uplink_window_side, config.uplink_window, placement.uplink)
    downlink = _Side(path.downlink_window_side, config.downlink_window, placement.downlink)
    return (uplink, downlink) if config.canonical_leg == uplink.leg else (downlink, uplink)


def _canonical_leg(config: BeamDirectionConfig) -> str:
    path = config.payload_path
    assert path is not None
    return config.canonical_leg or path.uplink_window_side


def _assignment_for(config: BeamDirectionConfig, window: Any) -> BeamSpectrumAssignment | None:
    from spectrum.selectors import active_assignments

    if window is None:
        return None
    found = active_assignments(config, window_id=window.pk)
    return found[0] if found else None


def _translation_of(config: BeamDirectionConfig) -> TranslationSpec:
    path = config.payload_path
    assert path is not None
    return TranslationSpec(
        method=TranslationMethod(path.translation_method),
        constant_hz=path.translation_constant_hz,
        spectral_inversion=config.inverts,
    )


def _guard_spec(guard_policy: Any, satnet: Satnet) -> GuardPolicySpec | None:
    """Resolve which policy applies. ADR-0016: override → Satnet → Window → system.

    Only the first two rungs are reachable from here; the Window's default and the system
    default are resolved inside ``calculations.guards`` from what it is handed, which is why a
    ``None`` return is a legitimate answer rather than a failure.
    """
    policy_record = guard_policy or satnet.default_guard_policy
    if policy_record is None:
        return None
    return GuardPolicySpec(
        mode=GuardMode(policy_record.mode),
        source=GuardSource.OVERRIDE if guard_policy else GuardSource.SATNET,
        label=policy_record.code,
        fixed_left_hz=policy_record.fixed_left_hz,
        fixed_right_hz=policy_record.fixed_right_hz,
        percent_left=policy_record.percent_left,
        percent_right=policy_record.percent_right,
    )


def _centre_of(occupied: FrequencyRange) -> int:
    return occupied.start_hz + occupied.width_hz // 2


def _acting_user(actor: Actor) -> User | None:
    """The actor as a stored user, or nothing.

    An `AnonymousUser` never reaches here — ``policy.require`` refuses first — but the column
    is nullable and the type says so, which keeps the service honest about the one case where
    a system action has no person behind it (the importer, in S15).
    """
    return actor if isinstance(actor, User) else None


def _occupancy(
    config: BeamDirectionConfig, side: _Side, assignment: BeamSpectrumAssignment
) -> spectrum_services.Occupancy:
    """One leg's occupancy, on **every** resource that leg competes on. **A-23**.

    Not one row per side — one row per *resource* per side. A leg sharing two RF chains writes
    two, and anything assuming a pair breaks the first time that happens.
    """
    resource_ids = tuple(
        str(link.spectrum_resource_id)
        for link in config.spectrum_resources.all()
        if link.spectrum_resource.leg == side.leg
    )
    return spectrum_services.Occupancy(
        assignment=assignment,
        leg=side.leg,
        polarization=side.window.polarization,
        occupied=side.placement.occupied,
        allocated=side.placement.allocated,
        resource_ids=resource_ids,
    )


def _check_containment(
    satnet: Satnet,
    config: BeamDirectionConfig,
    assignment: BeamSpectrumAssignment,
    requested: TimePeriod,
    canonical: _Side,
) -> list[Finding]:
    """**OQ-32**, delegated to the module that owns it."""
    verdict = containment.evaluate(
        satnet=satnet,
        beam=satnet.beam,
        assignment=assignment,
        requested=requested,
        direction=config.direction,
        polarization=canonical.window.polarization,
        operational=True,
    )
    return [
        Finding(
            code=finding.code,
            message=finding.message,
            rule="OQ-32 / ADR-0020",
            beam_code=satnet.beam.code,
        )
        for finding in verdict.findings
    ]


def _check_conflicts(
    satnet: Satnet,
    config: BeamDirectionConfig,
    side: _Side,
    assignment: BeamSpectrumAssignment,
    requested: TimePeriod,
    exclude_path_id: uuid.UUID | None,
) -> list[Finding]:
    """Everything §9.5 asks the blocking message to contain, for one leg.

    **Both legs are checked, and a conflict on either blocks** (§8.2). A translated-side-only
    collision is the case an operator cannot see: they chose an uplink centre that looks clear,
    and the downlink image lands on somebody else's transmission.
    """
    proposed = side.placement.allocated
    resource_ids = [
        str(link.spectrum_resource_id)
        for link in config.spectrum_resources.all()
        if link.spectrum_resource.leg == side.leg
    ]
    if not resource_ids:
        return [
            Finding(
                code="LEG_HAS_NO_SPECTRUM_RESOURCE",
                rule="OQ-25 / ADR-0018",
                beam_code=satnet.beam.code,
                window_code=side.window.code,
                message=(
                    f"The {side.leg} leg is not mapped to a spectrum resource, so nothing "
                    f"would compete with this allocation. It cannot be saved."
                ),
            )
        ]

    entitlement = FrequencyRange(assignment.rf_start_hz, assignment.rf_end_hz)
    findings: list[Finding] = []

    if not entitlement.contains(proposed):
        findings.append(
            Finding(
                code="OUTSIDE_ENTITLEMENT",
                rule="ADR-0019",
                beam_code=satnet.beam.code,
                window_code=side.window.code,
                proposed=(proposed.start_hz, proposed.end_hz),
                message=(
                    f"The allocated range on {side.leg} reaches outside this Beam's spectrum "
                    f"assignment ({entitlement.start_hz}-{entitlement.end_hz} Hz). Guards are "
                    f"part of what is reserved, so a guard at the edge needs a wider "
                    f"assignment."
                ),
                suggested_gaps=_gaps_for(config, side.leg),
            )
        )

    conflicts = []
    for row in spectrum_selectors.reservations_on(
        resource_ids, exclude_satnet_path_id=exclude_path_id
    ):
        held = FrequencyRange(row.allocated_start_hz, row.allocated_end_hz)
        if not proposed.overlaps(held):
            continue
        if not requested.overlaps(TimePeriod(row.valid_from, row.valid_until)):
            continue
        overlap = min(proposed.end_hz, held.end_hz) - max(proposed.start_hz, held.start_hz)
        conflicts.append(
            Conflict(
                resource_code=row.spectrum_resource.code,
                leg=row.leg,
                conflicting_path_code=str(row.satnet_path_id or "fixed reserve"),
                conflicting_range=(held.start_hz, held.end_hz),
                overlap_hz=overlap,
                validity_overlap=(
                    max(requested.start, row.valid_from),
                    _earliest(requested.end, row.valid_until),
                ),
            )
        )

    if conflicts:
        findings.append(
            Finding(
                code="SPECTRUM_CONFLICT",
                rule="section 8.1",
                beam_code=satnet.beam.code,
                window_code=side.window.code,
                proposed=(proposed.start_hz, proposed.end_hz),
                conflicts=tuple(conflicts),
                suggested_gaps=_gaps_for(config, side.leg),
                message=(
                    f"The allocated range {proposed.start_hz}-{proposed.end_hz} Hz on "
                    f"{side.leg} overlaps {len(conflicts)} existing allocation(s) on the "
                    f"spectrum resources this leg competes on."
                ),
            )
        )
    return findings


def _gaps_for(config: BeamDirectionConfig, leg: str) -> tuple[tuple[int, int], ...]:
    """The free intervals to offer alongside a refusal. §9.5.

    A refusal without somewhere to go is a dead end, and the operator's next action is always
    "then where can it go".
    """
    summary = spectrum_selectors.capacity(config, leg=leg)
    return tuple((gap.range.start_hz, gap.range.end_hz) for gap in summary.gaps[:5])


def _earliest(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
