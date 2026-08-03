"""Inventory authorization, enforced in the backend.

Specification sections 12 and 25. Every write is attempted as a direct HTTP POST rather
than by submitting a rendered form, so a hidden button can never be mistaken for an
enforced rule.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from audit.models import AuditEvent
from inventory.models import Band, Gateway, Satellite
from tests.factories import (
    TEST_PASSWORD,
    make_admin,
    make_approver,
    make_observer,
    make_operator,
)
from tests.inventory.factories import make_band, make_gateway

INDEX = "/inventory/"


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


def _satellite_payload(**overrides) -> dict:
    payload = {
        "code": "SAT-NEW",
        "name": "New satellite",
        "operator": "",
        "orbital_position": "",
        "orbit_type": "GEO",
        "effective_from": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "effective_until": "",
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
def test_every_role_may_read_inventory(client, factory):
    """An Operator picking a Beam needs to see the Satellite and Band behind it."""
    _sign_in(client, factory())

    assert client.get(INDEX).status_code == 200
    assert client.get("/inventory/satellites/").status_code == 200
    assert client.get("/inventory/bands/").status_code == 200
    assert client.get("/inventory/equipment-profiles/").status_code == 200


@pytest.mark.django_db
def test_anonymous_is_redirected_to_sign_in(client):
    response = client.get(INDEX)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_the_index_shows_the_independent_and_dependent_split(client):
    """Acceptance criterion 26.4."""
    _sign_in(client, make_operator())

    body = client.get(INDEX).content.decode()

    assert "Independent Data" in body
    assert "Dependent Data" in body
    # Independent entities are reachable; dependent ones are named but not yet linked.
    assert "/inventory/satellites/" in body
    assert "Frequency Windows" in body
    assert "Payload Paths" in body
    assert "Beams" in body


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_approver, make_observer])
def test_non_admins_cannot_create_by_direct_post(client, factory):
    _sign_in(client, factory())

    response = client.post("/inventory/satellite/new/", _satellite_payload())

    assert response.status_code == 403
    assert Satellite.objects.count() == 0


@pytest.mark.django_db
def test_admin_can_create(client):
    _sign_in(client, make_admin())

    response = client.post("/inventory/satellite/new/", _satellite_payload())

    assert response.status_code == 302
    satellite = Satellite.objects.get(code="SAT-NEW")
    assert satellite.name == "New satellite"


@pytest.mark.django_db
def test_creation_is_audited(client):
    admin = make_admin()
    _sign_in(client, admin)

    client.post("/inventory/satellite/new/", _satellite_payload(reason="Commissioning"))

    event = AuditEvent.objects.get(action="INVENTORY_CREATED")
    assert event.actor_id == admin.pk
    assert event.change_reason == "Commissioning"
    assert event.after["code"] == "SAT-NEW"


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_edit_by_direct_post(client, factory):
    band = make_band("BAND-X", name="Original")
    _sign_in(client, factory())

    response = client.post(
        f"/inventory/band/{band.pk}/edit/",
        {
            "code": "BAND-X",
            "name": "Tampered",
            "rf_min_hz": "27500",
            "rf_max_hz": "30000",
            "default_display_unit": "MHz",
            "expected_version": band.record_version,
        },
    )

    assert response.status_code == 403
    band.refresh_from_db()
    assert band.name == "Original"


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_deactivate_by_direct_post(client, factory):
    gateway = make_gateway()
    _sign_in(client, factory())

    response = client.post(f"/inventory/gateway/{gateway.pk}/deactivate/")

    assert response.status_code == 403
    gateway.refresh_from_db()
    assert gateway.is_active is True


@pytest.mark.django_db
def test_a_denied_write_is_audited(client):
    operator = make_operator()
    _sign_in(client, operator)

    client.post("/inventory/satellite/new/", _satellite_payload())

    assert AuditEvent.objects.filter(
        action="PERMISSION_DENIED", actor=operator, outcome="FAILURE"
    ).exists()


# ---------------------------------------------------------------------------
# Frequency entry
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_frequencies_are_entered_in_megahertz_and_stored_as_integer_hz(client):
    """ADR-0003. The form is the only place the conversion happens."""
    _sign_in(client, make_admin())

    client.post(
        "/inventory/band/new/",
        {
            "code": "KA",
            "name": "Ka band",
            "rf_min_hz": "27500.5",
            "rf_max_hz": "30000",
            "default_display_unit": "MHz",
            "tuning_raster_hz": "",
            "description": "",
            "reason": "test",
        },
    )

    band = Band.objects.get(code="KA")
    assert band.rf_min_hz == 27_500_500_000
    assert band.rf_max_hz == 30_000_000_000
    assert isinstance(band.rf_min_hz, int)


@pytest.mark.django_db
def test_a_sub_hertz_frequency_is_rejected(client):
    """Specification section 14.1 forbids binary floating point for engineering values.

    Silently rounding a value finer than 1 Hz would reintroduce exactly the imprecision
    integer Hz storage exists to prevent, so it is refused rather than truncated.
    """
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/band/new/",
        {
            "code": "BAD",
            "name": "Bad",
            "rf_min_hz": "27500.0000005",
            "rf_max_hz": "30000",
            "default_display_unit": "MHz",
            "reason": "test",
        },
    )

    assert response.status_code == 400
    assert Band.objects.filter(code="BAD").count() == 0


@pytest.mark.django_db
def test_a_mismatched_conversion_and_sideband_is_rejected_before_the_database(client):
    """The form mirrors the CHECK so the user sees a field error, not an error page.

    The database remains the authority; this is about the message, not the rule.
    """
    band = make_band()
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/equipment/new/",
        {
            "code": "BUC-BAD",
            "name": "Mismatched",
            "type": "BUC",
            "band": str(band.pk),
            "rf_min_hz": "29000",
            "rf_max_hz": "30000",
            "if_min_hz": "950",
            "if_max_hz": "1950",
            "lo_hz": "28050",
            "conversion_method": "LO_PLUS_IF",
            "sideband": "HIGH_SIDE",
            "priority": "100",
            "effective_from": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "reason": "test",
        },
    )

    assert response.status_code == 400
    assert "must agree" in response.content.decode()


# ---------------------------------------------------------------------------
# Gateway and Hub remain separate entities (section 3.1)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_gateway_and_hub_are_separate_entities():
    """Section 3.1: *"Gateway and Hub are separate operational entities. Define and
    document their relationship explicitly; do not merge them into one object."*"""
    from inventory.models import Hub
    from tests.inventory.factories import make_hub

    gateway = make_gateway("GW-A")
    hub_one = make_hub(gateway, "HUB-1", vendor="Vendor A")
    hub_two = make_hub(gateway, "HUB-2", vendor="Vendor B")

    # One site, several platform instances, possibly from different vendors.
    assert Hub.objects.filter(gateway=gateway).count() == 2
    assert hub_one.vendor != hub_two.vendor

    # A hub belongs to exactly one site: the relationship is Gateway 1 -- N Hubs, and
    # Hub.gateway is a required single foreign key rather than a many-to-many.
    gateway_field = Hub._meta.get_field("gateway")
    assert gateway_field.many_to_one
    assert not gateway_field.null

    # The two remain distinct tables with distinct attributes; neither is a view or a
    # proxy of the other.
    assert Gateway._meta.db_table == "gateway"
    assert Hub._meta.db_table == "hub"
    assert {"latitude", "longitude", "time_zone"} <= {f.name for f in Gateway._meta.fields}
    assert {"platform", "vendor"} <= {f.name for f in Hub._meta.fields}
