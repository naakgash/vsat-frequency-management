"""Database constraints on inventory master data.

Specification section 20. Each test attempts the violation through the ORM and expects the
database to refuse it — a test that only inspected the migration would pass even if the
constraint had been dropped.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory.constants import ConversionMethod, EquipmentType, OrbitType, Sideband
from inventory.models import Band, BandPolarization, EquipmentProfile, Hub, Satellite
from tests.inventory.factories import (
    add_polarization,
    make_band,
    make_equipment_profile,
    make_gateway,
    make_hub,
    make_satellite,
)


@pytest.mark.django_db
def test_band_rf_minimum_must_be_below_maximum():
    with pytest.raises(IntegrityError), transaction.atomic():
        Band.objects.create(
            code="BAD", name="Bad band", rf_min_hz=30_000_000_000, rf_max_hz=27_500_000_000
        )


@pytest.mark.django_db
def test_band_tuning_raster_must_be_positive_when_set():
    """OQ-31: NULL means unconfirmed, which is legitimate. Zero is not."""
    make_band("OK", tuning_raster_hz=None)  # unconfirmed is fine

    with pytest.raises(IntegrityError), transaction.atomic():
        make_band("BAD", tuning_raster_hz=0)


@pytest.mark.django_db
def test_satellite_effective_period_must_be_ordered():
    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Satellite.objects.create(
            code="BAD",
            name="Bad",
            orbit_type=OrbitType.GEO,
            effective_from=now,
            effective_until=now - timezone.timedelta(days=1),
        )


@pytest.mark.django_db
def test_an_open_ended_effective_period_is_allowed():
    satellite = make_satellite("OPEN", effective_until=None)

    assert satellite.effective_until is None


@pytest.mark.django_db
def test_gateway_latitude_must_be_in_range():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_gateway("BAD", latitude=91)


@pytest.mark.django_db
def test_gateway_longitude_must_be_in_range():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_gateway("BAD", longitude=-181)


@pytest.mark.django_db
def test_hub_code_is_unique_per_gateway_not_globally():
    """Assumption A-18: two sites may each have a "HUB-1"."""
    gateway_a, gateway_b = make_gateway("GW-A"), make_gateway("GW-B")

    make_hub(gateway_a, "HUB-1")
    make_hub(gateway_b, "HUB-1")  # same code, different site: allowed

    with pytest.raises(IntegrityError), transaction.atomic():
        make_hub(gateway_a, "HUB-1")


@pytest.mark.django_db
def test_the_composite_key_for_the_future_satnet_constraint_exists():
    """docs/design/04 section 3.2 pins Satnet.gateway_id to its Hub's Gateway with a
    composite foreign key. That needs UNIQUE (id, gateway_id) on hub, created now so the
    S10 migration does not have to alter a populated table."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'hub'::regclass "
            "AND conname = 'uq_hub_id_gateway'"
        )
        assert cursor.fetchone() is not None


@pytest.mark.django_db
def test_equipment_rf_and_if_ranges_must_be_ordered():
    band = make_band()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_equipment_profile(band, "BAD-RF", rf_min_hz=30_000_000_000, rf_max_hz=29_000_000_000)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_equipment_profile(band, "BAD-IF", if_min_hz=1_950_000_000, if_max_hz=950_000_000)


@pytest.mark.django_db
def test_equipment_lo_must_be_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_equipment_profile(code="BAD-LO", lo_hz=0)


@pytest.mark.django_db
def test_conversion_method_and_sideband_must_agree():
    """The constraint that makes the conversion algebra invertible.

    ``IF = |RF - LO|`` cannot be reversed without knowing which side the LO sits on. A
    profile claiming low-side injection while subtracting the IF from the LO would produce
    a silently wrong IF, so the pairing is enforced rather than trusted
    (docs/design/02 section 2.3).
    """
    band = make_band()

    with pytest.raises(IntegrityError), transaction.atomic():
        make_equipment_profile(
            band,
            "BAD-PAIR",
            conversion_method=ConversionMethod.LO_PLUS_IF,
            sideband=Sideband.HIGH_SIDE,
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        make_equipment_profile(
            band,
            "BAD-PAIR-2",
            conversion_method=ConversionMethod.LO_MINUS_IF,
            sideband=Sideband.LOW_SIDE,
        )


@pytest.mark.django_db
def test_valid_conversion_pairings_are_accepted():
    band = make_band()

    low = make_equipment_profile(
        band, "OK-LOW", conversion_method=ConversionMethod.LO_PLUS_IF, sideband=Sideband.LOW_SIDE
    )
    high = make_equipment_profile(
        band,
        "OK-HIGH",
        conversion_method=ConversionMethod.LO_MINUS_IF,
        sideband=Sideband.HIGH_SIDE,
        type=EquipmentType.LNB,
    )
    # A fixed offset carries no LO relationship, so either sideband is acceptable.
    fixed = make_equipment_profile(
        band,
        "OK-FIXED",
        conversion_method=ConversionMethod.FIXED_OFFSET,
        sideband=Sideband.LOW_SIDE,
    )

    assert low.is_inverting is False
    assert high.is_inverting is True
    assert fixed.is_inverting is False


@pytest.mark.django_db
def test_a_polarization_cannot_be_listed_twice_for_one_band():
    band = make_band()
    add_polarization(band, "RHCP")

    with pytest.raises(IntegrityError), transaction.atomic():
        add_polarization(band, "RHCP")


@pytest.mark.django_db
def test_deleting_a_gateway_with_hubs_is_refused():
    """Section 20: used inventory is never hard-deleted. PROTECT enforces it even against
    a direct ORM delete."""
    gateway = make_gateway()
    make_hub(gateway)

    with pytest.raises(IntegrityError), transaction.atomic():
        gateway.delete()


@pytest.mark.django_db
def test_deleting_a_band_with_equipment_profiles_is_refused():
    band = make_band()
    make_equipment_profile(band)

    with pytest.raises(IntegrityError), transaction.atomic():
        band.delete()


@pytest.mark.django_db
def test_no_inventory_is_seeded():
    """Specification section 26.20 and the whole point of the OPEN QUESTION register.

    Frequency Windows, translations, polarization mappings and equipment limits are all
    unconfirmed (OQ-01 to OQ-04, OQ-14). Shipping a plausible-looking Ka-band satellite
    with a made-up LO would be exactly the invention the specification forbids, and it
    would be indistinguishable from real data once loaded.
    """
    assert Satellite.objects.count() == 0
    assert Band.objects.count() == 0
    assert BandPolarization.objects.count() == 0
    assert Hub.objects.count() == 0
    assert EquipmentProfile.objects.count() == 0
