"""Beam write paths.

Same shape as every other service in the product (ADR-0013): **authorise, then transact**.
Authorisation inside the transaction would have its denial audit record rolled back along
with the failure.

Beam engineering is administrator-only (§25). An Operator picks a Beam when creating a
Satnet Path; they never configure one.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts import policy
from accounts.models import User
from accounts.types import Actor
from audit import services as audit_services
from beams import validation
from beams.constants import (
    ACTIVATION_BLOCKED,
    BEAM_ACTIVATED,
    BEAM_CREATED,
    BEAM_DEACTIVATED,
    BEAM_UPDATED,
    BEAM_VALIDATED,
    CANONICAL_LEG_DEFAULTS,
    MANAGE_BEAMS,
    ConfigurationState,
    Direction,
)
from beams.models import (
    Beam,
    BeamDirectionConfig,
    BeamDirectionEquipmentProfile,
    BeamValidationResult,
)


class StaleBeamError(Exception):
    """Raised when a form was rendered from a version that has since changed."""


class ActivationBlockedError(Exception):
    """Raised when activation is refused because the configuration is not valid.

    Carries the report, not just a message. §26.6 requires the refusal; an administrator
    then needs to know *which* rule failed, on *which* direction, or the refusal is a dead
    end.
    """

    def __init__(self, beam: Beam, report: validation.Report) -> None:
        self.beam = beam
        self.report = report
        reasons = (
            "; ".join(f"{f.direction or 'Beam'}: {f.message}" for f in report.blocking)
            or f"configuration is {report.state}"
        )
        super().__init__(f"{beam.code} cannot be activated. {reasons}")


def create(*, actor: Actor, values: dict[str, Any], reason: str = "") -> Beam:
    """Create a Beam and its two direction rows.

    Both directions are created up front, disabled-by-default for neither: they start
    *enabled and unconfigured*, which is `INCOMPLETE`. Creating only the direction someone
    happens to configure first would make "not configured yet" and "deliberately disabled"
    the same absence, and §5.4 requires them to be distinguishable.
    """
    policy.require(actor, MANAGE_BEAMS, reason=reason)
    return _create(actor=actor, values=values, reason=reason)


@transaction.atomic
def _create(*, actor: Actor, values: dict[str, Any], reason: str) -> Beam:
    beam = Beam(**values)
    beam.full_clean(exclude=["created_by", "updated_by", "activated_by"])
    beam.save()

    for direction in Direction.values:
        BeamDirectionConfig.objects.create(
            beam=beam,
            direction=direction,
            canonical_leg=CANONICAL_LEG_DEFAULTS[direction],
        )

    audit_services.record(
        action=BEAM_CREATED,
        actor=actor,
        obj=beam,
        after=audit_services.snapshot(beam),
        change_reason=reason,
        message=f"Created Beam {beam}",
    )
    return beam


def update_direction(
    *,
    actor: Actor,
    config: BeamDirectionConfig,
    values: dict[str, Any],
    equipment: list[tuple[Any, int]] | None = None,
    reason: str = "",
) -> BeamDirectionConfig:
    """Configure one direction chain.

    Re-validates the whole Beam afterwards and caches the state. Validating only the edited
    direction would leave the Beam's badge stale whenever a rule spans both — the satellite
    check does, and so does "at least one direction is enabled".
    """
    policy.require(actor, MANAGE_BEAMS, config.beam, reason=reason)
    return _update_direction(
        actor=actor, config=config, values=values, equipment=equipment, reason=reason
    )


@transaction.atomic
def _update_direction(
    *,
    actor: Actor,
    config: BeamDirectionConfig,
    values: dict[str, Any],
    equipment: list[tuple[Any, int]] | None,
    reason: str,
) -> BeamDirectionConfig:
    before = audit_services.snapshot(config)

    for field, value in values.items():
        setattr(config, field, value)
    config.full_clean(exclude=["created_by", "updated_by"])
    config.save()

    if equipment is not None:
        _replace_equipment(config, equipment)

    audit_services.record(
        action=BEAM_UPDATED,
        actor=actor,
        obj=config.beam,
        before=before,
        after=audit_services.snapshot(config),
        change_reason=reason,
        message=f"Configured {config.beam.code} {config.direction}",
    )
    _refresh_state(config.beam)
    return config


def _replace_equipment(config: BeamDirectionConfig, equipment: list[tuple[Any, int]]) -> None:
    """Set the candidate pool to exactly this list.

    Replace rather than merge: the form shows the whole set, so what the administrator sees
    is what they mean. A merge would make removing a profile impossible through the wizard.
    """
    chosen = {profile.pk: priority for profile, priority in equipment}
    config.equipment_profiles.exclude(equipment_profile__in=chosen).delete()

    existing = {entry.equipment_profile_id: entry for entry in config.equipment_profiles.all()}
    for profile, priority in equipment:
        entry = existing.get(profile.pk)
        if entry is None:
            BeamDirectionEquipmentProfile.objects.create(
                direction_config=config, equipment_profile=profile, priority=priority
            )
        elif entry.priority != priority:
            entry.priority = priority
            entry.save(update_fields=["priority"])


def validate_beam(*, actor: Actor, beam: Beam, reason: str = "") -> BeamValidationResult:
    """Run every rule and keep the result. Specification sections 10.1 and 18.

    Readable by anyone who may view the Beam: knowing whether a Beam is valid is part of
    reading it, and running the check changes no configuration.
    """
    policy.require(actor, "beams.view_beam", beam, reason=reason)
    return _validate_beam(actor=actor, beam=beam, reason=reason)


@transaction.atomic
def _validate_beam(*, actor: Actor, beam: Beam, reason: str) -> BeamValidationResult:
    report = validation.validate(beam)
    result = BeamValidationResult.objects.create(
        beam=beam,
        ran_by=_acting_user(actor),
        outcome=report.outcome,
        configuration_state=report.state,
        findings=[f.as_dict() for f in report.findings],
    )
    _store_state(beam, report.state)

    audit_services.record(
        action=BEAM_VALIDATED,
        actor=actor,
        obj=beam,
        after={"outcome": report.outcome, "state": report.state},
        change_reason=reason,
        message=f"Validated Beam {beam.code}: {report.outcome}",
    )
    return result


def set_active(*, actor: Actor, beam: Beam, active: bool, reason: str = "") -> Beam:
    """Activate or deactivate a Beam. Specification section 26.6.

    **Activation re-validates rather than trusting the cached state.** The master data
    underneath a Beam can be superseded between the last validation run and the button
    press, so a stored `VALID` is evidence of what was true earlier, not of what is true
    now. This is the one place that distinction matters enough to pay for the extra work.
    """
    policy.require(actor, MANAGE_BEAMS, beam, reason=reason)

    if active:
        result = _validate_beam(actor=actor, beam=beam, reason=reason)
        report = validation.validate(beam)
        if not report.is_activatable:
            # Refusals are audited. §26.6 makes the refusal a requirement, and a refusal
            # nobody can find afterwards is indistinguishable from a broken button.
            audit_services.record(
                action=ACTIVATION_BLOCKED,
                actor=actor,
                obj=beam,
                after={"state": report.state, "blocking": [f.code for f in report.blocking]},
                change_reason=reason,
                outcome="FAILURE",
                message=f"Refused to activate Beam {beam.code}: {report.state}",
            )
            raise ActivationBlockedError(beam, report)
        result.activated = True
        result.save(update_fields=["activated"])

    return _set_active(actor=actor, beam=beam, active=active, reason=reason)


@transaction.atomic
def _set_active(*, actor: Actor, beam: Beam, active: bool, reason: str) -> Beam:
    before = {"is_active": beam.is_active}
    beam.is_active = active
    if active:
        beam.activated_at = timezone.now()
        beam.activated_by = _acting_user(actor)
    # activated_at and activated_by are deliberately *not* cleared on deactivation: they
    # record that an activation happened, and erasing them would remove the evidence for a
    # Satnet Path that was created while the Beam was live.
    beam.save(update_fields=["is_active", "activated_at", "activated_by", "updated_at"])

    audit_services.record(
        action=BEAM_ACTIVATED if active else BEAM_DEACTIVATED,
        actor=actor,
        obj=beam,
        before=before,
        after={"is_active": active},
        change_reason=reason,
        message=f"{'Activated' if active else 'Deactivated'} Beam {beam.code}",
    )
    return beam


def _refresh_state(beam: Beam) -> None:
    """Recompute and cache the configuration state after an edit."""
    _store_state(beam, validation.validate(beam).state)


def _store_state(beam: Beam, state: ConfigurationState) -> None:
    if beam.configuration_state == state:
        return
    beam.configuration_state = state
    beam.save(update_fields=["configuration_state", "updated_at"])


def _acting_user(actor: Actor) -> User | None:
    return actor if isinstance(actor, User) else None
