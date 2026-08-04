"""Beam activation. Specification section 26.6, the acceptance criterion this slice exists for.

*A Beam cannot be activated while its mandatory FWD/RTN configuration is invalid.*

The rule is enforced three times over, and each layer is tested separately because each can
fail on its own: the service refuses with reasons, the database makes the state unreachable,
and the interface disables the button. Only the first two are guarantees.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from beams import services, validation
from beams.constants import ConfigurationState, Direction
from beams.models import Beam, BeamValidationResult
from inventory.constants import PolarizationType
from tests.beams.factories import configure_direction, make_beam, make_valid_beam
from tests.factories import make_admin, make_operator


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_valid_beam_activates():
    admin = make_admin()
    beam = make_valid_beam()

    services.set_active(actor=admin, beam=beam, active=True, reason="Commissioning")

    beam.refresh_from_db()
    assert beam.is_active
    assert beam.activated_at is not None
    assert beam.activated_by_id == admin.pk


@pytest.mark.django_db
def test_an_incomplete_beam_cannot_be_activated():
    admin = make_admin()
    beam = make_beam()

    with pytest.raises(services.ActivationBlockedError):
        services.set_active(actor=admin, beam=beam, active=True)

    beam.refresh_from_db()
    assert not beam.is_active


@pytest.mark.django_db
def test_an_invalid_beam_cannot_be_activated():
    admin = make_admin()
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        downlink_polarization=PolarizationType.LHCP
    )

    with pytest.raises(services.ActivationBlockedError):
        services.set_active(actor=admin, beam=beam, active=True)

    beam.refresh_from_db()
    assert not beam.is_active


@pytest.mark.django_db
def test_the_refusal_names_the_rule_that_failed():
    """A refusal that only says "no" leaves an administrator with nothing to fix."""
    admin = make_admin()
    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        downlink_polarization=PolarizationType.LHCP
    )

    with pytest.raises(services.ActivationBlockedError) as excinfo:
        services.set_active(actor=admin, beam=beam, active=True)

    assert "FWD" in str(excinfo.value)
    assert excinfo.value.report.blocking


@pytest.mark.django_db
def test_a_refused_activation_is_audited():
    """Section 26.6 makes the refusal a requirement, and a refusal nobody can find
    afterwards is indistinguishable from a broken button."""
    admin = make_admin()
    beam = make_beam()

    with pytest.raises(services.ActivationBlockedError):
        services.set_active(actor=admin, beam=beam, active=True, reason="Trying it on")

    event = AuditEvent.objects.get(action="BEAM_ACTIVATION_BLOCKED")
    assert event.actor_id == admin.pk
    assert event.outcome == "FAILURE"
    assert event.change_reason == "Trying it on"


# ---------------------------------------------------------------------------
# Activation re-validates
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_activation_revalidates_rather_than_trusting_the_cached_state():
    """The master data underneath a Beam can be superseded between the last validation run
    and the button press, so a stored VALID is evidence of what was true earlier."""
    admin = make_admin()
    beam = make_valid_beam()
    services.validate_beam(actor=admin, beam=beam)
    beam.refresh_from_db()
    assert beam.configuration_state == ConfigurationState.VALID

    # Break it *without* going through a service, exactly as a superseded window would.
    beam.direction_configs.filter(direction=Direction.FWD).update(canonical_leg="")

    with pytest.raises(services.ActivationBlockedError):
        services.set_active(actor=admin, beam=beam, active=True)


@pytest.mark.django_db
def test_activation_records_the_run_that_justified_it():
    """Section 18: "the Beam was valid when we activated it" is a claim unless the run that
    said so still exists."""
    admin = make_admin()
    beam = make_valid_beam()

    services.set_active(actor=admin, beam=beam, active=True)

    result = BeamValidationResult.objects.filter(beam=beam, activated=True).get()
    assert result.configuration_state == ConfigurationState.VALID


# ---------------------------------------------------------------------------
# The database's half of the rule
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_database_refuses_an_active_beam_that_is_not_valid():
    """The service refuses with reasons; this makes the state unreachable by any other
    route, including a direct SQL update or the importer in S15.

    The activation record is supplied so that *only* the configuration constraint is
    violated. Both CHECKs would otherwise fire on the same row and PostgreSQL reports
    whichever it evaluates first, which would make this test pass for the wrong reason.
    """
    admin = make_admin()
    beam = make_beam()  # INCOMPLETE

    with pytest.raises(IntegrityError, match="ck_beam_active_requires_valid_configuration"):
        with transaction.atomic():
            Beam.objects.filter(pk=beam.pk).update(
                is_active=True, activated_at=timezone.now(), activated_by=admin
            )


@pytest.mark.django_db
def test_the_database_refuses_an_active_beam_with_no_activation_record():
    """Without this an activation could be faked by flipping the boolean, and section 18's
    trail would have nothing to show."""
    beam = make_valid_beam()
    beam.configuration_state = ConfigurationState.VALID
    beam.save()

    with pytest.raises(IntegrityError, match="ck_beam_active_has_activation_record"):
        with transaction.atomic():
            Beam.objects.filter(pk=beam.pk).update(is_active=True)


@pytest.mark.django_db
def test_a_chain_must_be_all_three_references_or_none():
    """A direction holding a payload path but no windows is not partly finished — its
    windows disagree with its path by construction, which A-06 forbids outright."""
    beam = make_beam()
    config = configure_direction(beam, Direction.FWD)

    with pytest.raises(IntegrityError, match="ck_beam_direction_chain_all_or_nothing"):
        with transaction.atomic():
            beam.direction_configs.filter(pk=config.pk).update(uplink_window=None)


# ---------------------------------------------------------------------------
# Deactivation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_deactivation_keeps_the_activation_record():
    """They record that an activation happened. Erasing them would remove the evidence for a
    Satnet Path created while the Beam was live."""
    admin = make_admin()
    beam = make_valid_beam()
    services.set_active(actor=admin, beam=beam, active=True)

    services.set_active(actor=admin, beam=beam, active=False, reason="Retired")

    beam.refresh_from_db()
    assert not beam.is_active
    assert beam.activated_at is not None
    assert beam.activated_by_id == admin.pk


@pytest.mark.django_db
def test_deactivation_is_never_blocked_by_validation():
    """Turning something off must always be possible. A Beam that has gone invalid is
    exactly the one someone most needs to be able to deactivate."""
    admin = make_admin()
    beam = make_valid_beam()
    services.set_active(actor=admin, beam=beam, active=True)
    beam.direction_configs.filter(direction=Direction.FWD).update(canonical_leg="")

    services.set_active(actor=admin, beam=beam, active=False)

    beam.refresh_from_db()
    assert not beam.is_active


# ---------------------------------------------------------------------------
# Authorisation — section 25
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_operator_cannot_activate_a_beam():
    """Beam engineering is administrator-only. An Operator picks a Beam; they never
    configure one."""
    from accounts.policy import PermissionDenied

    beam = make_valid_beam()

    with pytest.raises(PermissionDenied):
        services.set_active(actor=make_operator(), beam=beam, active=True)

    beam.refresh_from_db()
    assert not beam.is_active


@pytest.mark.django_db
def test_an_operator_may_validate_a_beam():
    """Knowing whether a Beam is valid is part of reading it, and running the check changes
    no configuration."""
    beam = make_valid_beam()

    result = services.validate_beam(actor=make_operator(), beam=beam)

    assert result.configuration_state == ConfigurationState.VALID


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_only_active_and_valid_beams_are_selectable():
    """The read-side half of section 26.6: a Beam that has gone invalid since activation
    cannot be attached to a new Satnet."""
    from beams import selectors

    admin = make_admin()
    live = make_valid_beam("BEAM-LIVE")
    services.set_active(actor=admin, beam=live, active=True)
    make_valid_beam("BEAM-DRAFT")

    assert [b.code for b in selectors.selectable(admin)] == ["BEAM-LIVE"]


@pytest.mark.django_db
def test_validation_caches_the_state_on_the_beam():
    """So a list of fifty Beams renders fifty badges without running fifty validations."""
    admin = make_admin()
    beam = make_valid_beam()
    assert beam.configuration_state == ConfigurationState.INCOMPLETE

    services.validate_beam(actor=admin, beam=beam)

    beam.refresh_from_db()
    assert beam.configuration_state == ConfigurationState.VALID
    assert validation.validate(beam).state is ConfigurationState.VALID
