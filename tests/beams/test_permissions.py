"""Beam authorization, enforced in the backend. Specification sections 12 and 25.

Every write is attempted as a direct POST rather than by submitting a rendered form, so a
hidden button can never be mistaken for an enforced rule.

§25 is the rule under test: Beam engineering is administrator-only. An Operator selects a
Beam when creating a Satnet Path; they never configure one.
"""

from __future__ import annotations

import pytest

from audit.models import AuditEvent
from beams.constants import Direction
from beams.models import Beam
from tests.beams.factories import make_valid_beam
from tests.factories import (
    TEST_PASSWORD,
    make_admin,
    make_approver,
    make_observer,
    make_operator,
)
from tests.inventory.factories import make_band, make_satellite

LIST = "/beams/"


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


def _identity_payload(satellite, band, **overrides) -> dict:
    payload = {
        "code": "BEAM-NEW",
        "name": "New beam",
        "satellite": str(satellite.pk),
        "band": str(band.pk),
        "coverage": "",
        "description": "",
        "engineering_reference": "",
        "reason": "test",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_admin, make_operator, make_approver, make_observer])
def test_every_role_may_read_beams(client, factory):
    """An Operator has to choose a Beam when creating a Satnet Path."""
    _sign_in(client, factory())

    assert client.get(LIST).status_code == 200


@pytest.mark.django_db
def test_anonymous_is_redirected_to_sign_in(client):
    response = client.get(LIST)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_an_operator_may_open_a_beam_detail_page(client):
    beam = make_valid_beam()
    _sign_in(client, make_operator())

    assert client.get(beam.get_absolute_url()).status_code == 200


@pytest.mark.django_db
def test_the_detail_page_validates_live_rather_than_reading_the_cached_state(client):
    """The master data underneath a Beam can be superseded after it was last checked, so a
    badge from last week is a claim about last week."""
    beam = make_valid_beam()
    Beam.objects.filter(pk=beam.pk).update(configuration_state="VALID")
    beam.direction_configs.filter(direction=Direction.FWD).update(canonical_leg="")
    _sign_in(client, make_operator())

    response = client.get(beam.get_absolute_url())

    assert (
        response.context["report"].state == "INCOMPLETE"
        or not response.context["report"].is_activatable
    )


# ---------------------------------------------------------------------------
# Writing — section 25
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_approver, make_observer])
def test_non_admins_cannot_create_a_beam_by_direct_post(client, factory):
    satellite, band = make_satellite(), make_band()
    _sign_in(client, factory())

    response = client.post("/beams/new/", _identity_payload(satellite, band))

    assert response.status_code == 403
    assert Beam.objects.count() == 0


@pytest.mark.django_db
def test_an_admin_can_create_a_beam(client):
    satellite, band = make_satellite(), make_band()
    _sign_in(client, make_admin())

    response = client.post("/beams/new/", _identity_payload(satellite, band))

    assert response.status_code == 302
    beam = Beam.objects.get(code="BEAM-NEW")
    # Both directions exist from creation, so "not configured yet" and "deliberately
    # disabled" are never the same absence (§5.4).
    assert set(beam.direction_configs.values_list("direction", flat=True)) == set(Direction.values)


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_reach_the_builder(client, factory):
    """§25. An Operator cannot edit Beam engineering data by any route."""
    beam = make_valid_beam()
    _sign_in(client, factory())

    for url in (
        f"/beams/{beam.pk}/build/FWD/",
        f"/beams/{beam.pk}/build/activate/",
    ):
        assert client.get(url).status_code == 403, url


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_configure_a_direction_by_direct_post(client, factory):
    beam = make_valid_beam()
    _sign_in(client, factory())

    response = client.post(
        f"/beams/{beam.pk}/build/FWD/", {"is_enabled": "on", "reason": "tampering"}
    )

    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_approver, make_observer])
def test_non_admins_cannot_activate_a_beam_by_direct_post(client, factory):
    beam = make_valid_beam()
    _sign_in(client, factory())

    response = client.post(f"/beams/{beam.pk}/build/activate/", {"action": "activate"})

    assert response.status_code == 403
    beam.refresh_from_db()
    assert not beam.is_active


@pytest.mark.django_db
def test_a_denied_write_is_audited(client):
    operator = make_operator()
    satellite, band = make_satellite(), make_band()
    _sign_in(client, operator)

    client.post("/beams/new/", _identity_payload(satellite, band))

    assert AuditEvent.objects.filter(
        action="PERMISSION_DENIED", actor=operator, outcome="FAILURE"
    ).exists()


@pytest.mark.django_db
def test_an_operator_may_run_a_validation(client):
    """Knowing whether a Beam is valid is part of reading it, and running the check changes
    no configuration."""
    beam = make_valid_beam()
    _sign_in(client, make_operator())

    response = client.post(f"/beams/{beam.pk}/build/validate/", {"reason": "checking"})

    assert response.status_code == 302
    assert beam.validation_results.exists()


# ---------------------------------------------------------------------------
# Activation over HTTP — section 26.6
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_activating_an_invalid_beam_over_http_is_refused_with_reasons(client):
    from inventory.constants import PolarizationType

    beam = make_valid_beam()
    beam.direction_configs.filter(direction=Direction.FWD).update(
        downlink_polarization=PolarizationType.LHCP
    )
    _sign_in(client, make_admin())

    response = client.post(f"/beams/{beam.pk}/build/activate/", {"action": "activate"})

    assert response.status_code == 409
    assert "cannot be activated" in response.content.decode()
    beam.refresh_from_db()
    assert not beam.is_active


@pytest.mark.django_db
def test_activating_a_valid_beam_over_http_succeeds(client):
    beam = make_valid_beam()
    _sign_in(client, make_admin())

    response = client.post(
        f"/beams/{beam.pk}/build/activate/", {"action": "activate", "reason": "Commissioning"}
    )

    assert response.status_code == 302
    beam.refresh_from_db()
    assert beam.is_active


@pytest.mark.django_db
def test_the_builder_walks_from_identity_through_both_chains(client):
    """The wizard's shape, section 5. Each step hands to the next rather than dropping the
    administrator back on a list."""
    satellite, band = make_satellite(), make_band()
    _sign_in(client, make_admin())

    created = client.post("/beams/new/", _identity_payload(satellite, band))
    beam = Beam.objects.get(code="BEAM-NEW")

    assert created["Location"] == f"/beams/{beam.pk}/build/FWD/"
    assert client.get(created["Location"]).status_code == 200


@pytest.mark.django_db
def test_an_unknown_direction_is_not_found(client):
    beam = make_valid_beam()
    _sign_in(client, make_admin())

    assert client.get(f"/beams/{beam.pk}/build/SIDEWAYS/").status_code == 404
