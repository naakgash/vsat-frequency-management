"""Dependency summaries and the deactivation guard.

Specification sections 3 and 3.2: dependency summaries on detail screens, and the
interface must *"prevent invalid deletion or deactivation when an object is in use"*.
"""

from __future__ import annotations

import pytest

from inventory import dependencies, services
from inventory.models import Gateway, Satellite
from tests.factories import make_admin
from tests.inventory.factories import make_band, make_equipment_profile, make_gateway, make_hub


@pytest.mark.django_db
def test_a_gateway_reports_its_hubs():
    gateway = make_gateway()
    make_hub(gateway, "HUB-1")
    make_hub(gateway, "HUB-2")

    summary = {d.label: d.count for d in dependencies.summarise(gateway)}

    assert summary["Hubs"] == 2


@pytest.mark.django_db
def test_zero_counts_are_reported_rather_than_omitted():
    """ "0 Hubs" tells an administrator the site is safe to deactivate. An omitted row is
    ambiguous between "none" and "not checked"."""
    gateway = make_gateway()

    labels = {d.label: d.count for d in dependencies.summarise(gateway)}

    assert labels["Hubs"] == 0
    assert labels["Equipment Profiles"] == 0


@pytest.mark.django_db
def test_a_band_reports_its_equipment_profiles():
    band = make_band()
    make_equipment_profile(band, "BUC-1")

    summary = {d.label: d.count for d in dependencies.summarise(band)}

    assert summary["Equipment Profiles"] == 1


@pytest.mark.django_db
def test_deactivating_an_object_in_use_is_refused():
    admin = make_admin()
    gateway = make_gateway()
    make_hub(gateway)

    with pytest.raises(services.InUseError) as excinfo:
        services.set_active(actor=admin, instance=gateway, active=False)

    assert "1 Hubs" in str(excinfo.value)
    gateway.refresh_from_db()
    assert gateway.is_active is True


@pytest.mark.django_db
def test_deactivating_an_unused_object_is_permitted():
    admin = make_admin()
    gateway = make_gateway()

    services.set_active(actor=admin, instance=gateway, active=False, reason="Site closed")

    gateway.refresh_from_db()
    assert gateway.is_active is False


@pytest.mark.django_db
def test_reactivation_is_never_blocked():
    """The guard protects against orphaning dependents, which reactivation cannot do."""
    admin = make_admin()
    gateway = make_gateway()
    services.set_active(actor=admin, instance=gateway, active=False)
    make_hub(gateway)

    services.set_active(actor=admin, instance=gateway, active=True)

    gateway.refresh_from_db()
    assert gateway.is_active is True


@pytest.mark.django_db
def test_deactivation_is_audited():
    from audit.models import AuditEvent

    admin = make_admin()
    gateway = make_gateway()

    services.set_active(actor=admin, instance=gateway, active=False, reason="Site closed")

    event = AuditEvent.objects.get(action="INVENTORY_DEACTIVATED")
    assert event.actor_id == admin.pk
    assert event.before == {"is_active": True}
    assert event.after == {"is_active": False}
    assert event.change_reason == "Site closed"


@pytest.mark.django_db
def test_an_informational_dependency_does_not_block():
    """Not every reference should stop a change. A registration may declare itself
    informational, and the summary shows the count without the guard acting on it."""
    gateway = make_gateway()
    dependencies.register(
        Gateway,
        label="Historical references",
        count=lambda g: 3,
        blocks_deactivation=False,
    )
    try:
        summary = {d.label: d for d in dependencies.summarise(gateway)}
        assert summary["Historical references"].count == 3
        assert dependencies.is_in_use(gateway) is False
    finally:
        # Restore the registry so the count does not leak into other tests.
        from django.apps import apps as django_apps

        dependencies.clear(Gateway)
        django_apps.get_app_config("inventory").ready()


@pytest.mark.django_db
def test_registering_the_same_label_twice_replaces_rather_than_duplicates():
    """AppConfig.ready() can run more than once under autoreload."""
    gateway = make_gateway()
    make_hub(gateway)

    from django.apps import apps as django_apps

    django_apps.get_app_config("inventory").ready()
    django_apps.get_app_config("inventory").ready()

    labels = [d.label for d in dependencies.summarise(gateway)]

    assert labels.count("Hubs") == 1


@pytest.mark.django_db
def test_models_with_no_registered_dependency_summarise_empty():
    """A Beam has no dependants until Satnets land in S10.

    The subject of this test moves up the stack with each slice, which is the registry
    working: S5 used a Payload Path, and S8's Beam direction configs made that no longer
    true. Anything still at the top of the graph will do.
    """
    from tests.beams.factories import make_beam

    beam = make_beam()

    assert dependencies.summarise(beam) == []
    assert dependencies.is_in_use(beam) is False


@pytest.mark.django_db
def test_a_model_with_registrations_but_no_dependants_reports_zeros():
    """Zero is not the same as unregistered.

    "0 Frequency Windows" tells an administrator the Satellite is safe to deactivate; an
    empty summary is ambiguous between "none" and "nothing checked" (section 3.2).
    """
    satellite = Satellite.objects.create(
        code="SAT-X", name="X", orbit_type="GEO", effective_from="2026-01-01T00:00:00Z"
    )

    summary = {d.label: d.count for d in dependencies.summarise(satellite)}

    assert summary == {
        "Frequency Windows": 0,
        "Payload Paths": 0,
        "Spectrum Resources": 0,
        "Beams": 0,
    }
    assert dependencies.is_in_use(satellite) is False
