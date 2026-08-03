"""Database constraints on dependent inventory.

Specification sections 13.6, 13.7 and 20. The composite foreign keys are the point of this
file: they are what turn "a FWD path runs hub uplink to remote downlink" from a convention
the application maintains into a fact the database enforces.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory.constants import Direction, GuardMode, PolarizationType, SpectrumLeg
from inventory.models import (
    FrequencyWindow,
    GuardPolicy,
    PayloadPath,
    PayloadPolarizationMapping,
)
from tests.inventory.factories import (
    make_band,
    make_frequency_window,
    make_payload_path,
    make_satellite,
)


# ---------------------------------------------------------------------------
# Frequency Window
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_window_start_must_be_below_end():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_frequency_window(rf_start_hz=29_500_000_000, rf_end_hz=29_000_000_000)


@pytest.mark.django_db
def test_window_edge_guard_cannot_be_negative():
    with pytest.raises(IntegrityError), transaction.atomic():
        make_frequency_window(min_edge_guard_hz=-1)


@pytest.mark.django_db
def test_window_ranges_are_half_open():
    """Specification section 8.4. The upper edge is exclusive, which is what makes two
    adjacent windows unambiguous."""
    window = make_frequency_window(rf_start_hz=29_000_000_000, rf_end_hz=29_500_000_000)

    assert window.contains(29_000_000_000, 29_500_000_000)
    assert window.contains(29_100_000_000, 29_200_000_000)
    assert not window.contains(28_999_999_999, 29_100_000_000)
    assert not window.contains(29_400_000_000, 29_500_000_001)
    assert window.width_hz == 500_000_000


@pytest.mark.django_db
def test_a_window_carries_exactly_one_polarization():
    """Assumption A-04 and specification section 25: two polarizations on one satellite
    leg are two windows, not one window with a set."""
    field = FrequencyWindow._meta.get_field("polarization")

    assert not field.many_to_many
    assert field.choices == PolarizationType.choices


# ---------------------------------------------------------------------------
# Payload Path — direction and window sides
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_forward_path_runs_hub_uplink_to_remote_downlink():
    path = make_payload_path(direction=Direction.FWD)

    assert path.uplink_window_side == SpectrumLeg.HUB_UPLINK
    assert path.downlink_window_side == SpectrumLeg.REMOTE_DOWNLINK


@pytest.mark.django_db
def test_a_return_path_runs_remote_uplink_to_hub_downlink():
    path = make_payload_path(direction=Direction.RTN, code="PP-RTN")

    assert path.uplink_window_side == SpectrumLeg.REMOTE_UPLINK
    assert path.downlink_window_side == SpectrumLeg.HUB_DOWNLINK


@pytest.mark.django_db
def test_a_path_whose_sides_contradict_its_direction_is_refused():
    """Specification section 20: "Frequency Window side consistency"."""
    satellite = make_satellite()
    band = make_band()
    uplink = make_frequency_window(satellite, "UL", SpectrumLeg.HUB_UPLINK, band=band)
    downlink = make_frequency_window(
        satellite,
        "DL",
        SpectrumLeg.REMOTE_DOWNLINK,
        band=band,
        rf_start_hz=19_000_000_000,
        rf_end_hz=19_500_000_000,
    )

    with pytest.raises(IntegrityError, match="ck_path_direction_sides"), transaction.atomic():
        PayloadPath.objects.create(
            code="BAD",
            name="Return path wearing forward sides",
            satellite=satellite,
            direction=Direction.RTN,  # contradicts the sides below
            uplink_window=uplink,
            downlink_window=downlink,
            uplink_window_side=SpectrumLeg.HUB_UPLINK,
            downlink_window_side=SpectrumLeg.REMOTE_DOWNLINK,
            translation_method="OFFSET_SUBTRACT",
            translation_constant_hz=10_000_000_000,
            effective_from=timezone.now(),
        )


@pytest.mark.django_db
def test_a_lying_side_column_is_refused_by_the_composite_foreign_key():
    """The constraint that makes the CHECK above worth anything.

    Without it a row could claim ``HUB_UPLINK`` while pointing at a remote downlink
    window, satisfy the direction CHECK, and be wrong. The composite key requires the
    (window, side) pair to exist as an actual window.
    """
    satellite = make_satellite()
    band = make_band()
    # A genuine remote-downlink window...
    downlink = make_frequency_window(
        satellite,
        "DL",
        SpectrumLeg.REMOTE_DOWNLINK,
        band=band,
        rf_start_hz=19_000_000_000,
        rf_end_hz=19_500_000_000,
    )
    uplink = make_frequency_window(satellite, "UL", SpectrumLeg.HUB_UPLINK, band=band)

    with pytest.raises(IntegrityError) as excinfo, transaction.atomic():
        PayloadPath.objects.create(
            code="LIAR",
            name="Claims the downlink window is a hub uplink",
            satellite=satellite,
            direction=Direction.FWD,
            # ...pointed at by the uplink slot, with a side that satisfies the CHECK but
            # does not match the window it names.
            uplink_window=downlink,
            downlink_window=uplink,
            uplink_window_side=SpectrumLeg.HUB_UPLINK,
            downlink_window_side=SpectrumLeg.REMOTE_DOWNLINK,
            translation_method="OFFSET_SUBTRACT",
            translation_constant_hz=10_000_000_000,
            effective_from=timezone.now(),
        )

    assert "fk_path_uplink_window_side" in str(excinfo.value)


@pytest.mark.django_db
def test_a_path_cannot_use_one_window_for_both_sides():
    satellite = make_satellite()
    window = make_frequency_window(satellite, "UL", SpectrumLeg.HUB_UPLINK)

    with pytest.raises(IntegrityError), transaction.atomic():
        PayloadPath.objects.create(
            code="SAME",
            name="Same window twice",
            satellite=satellite,
            direction=Direction.FWD,
            uplink_window=window,
            downlink_window=window,
            uplink_window_side=SpectrumLeg.HUB_UPLINK,
            downlink_window_side=SpectrumLeg.HUB_UPLINK,
            translation_method="OFFSET_SUBTRACT",
            translation_constant_hz=10_000_000_000,
            effective_from=timezone.now(),
        )


@pytest.mark.django_db
def test_deleting_a_window_referenced_by_a_path_is_refused():
    """Section 20: used inventory is never hard-deleted."""
    path = make_payload_path()

    with pytest.raises(IntegrityError), transaction.atomic():
        path.uplink_window.delete()


# ---------------------------------------------------------------------------
# Guard Policy
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_guard_policy_must_carry_the_values_its_mode_needs():
    """A policy missing its own values would resolve to a zero guard silently, which is
    the one failure mode a guard must not have."""
    with (
        pytest.raises(IntegrityError, match="ck_guard_mode_has_required_values"),
        transaction.atomic(),
    ):
        GuardPolicy.objects.create(code="EMPTY", name="Fixed with no widths", mode=GuardMode.FIXED)

    with pytest.raises(IntegrityError), transaction.atomic():
        GuardPolicy.objects.create(
            code="HALF",
            name="Percent with only one side",
            mode=GuardMode.PERCENT_OF_OCCUPIED,
            percent_left=5,
        )


@pytest.mark.django_db
def test_each_guard_mode_accepts_a_complete_policy():
    fixed = GuardPolicy.objects.create(
        code="F", name="Fixed", mode=GuardMode.FIXED, fixed_left_hz=0, fixed_right_hz=0
    )
    percent = GuardPolicy.objects.create(
        code="P",
        name="Percent",
        mode=GuardMode.PERCENT_OF_OCCUPIED,
        percent_left=5,
        percent_right=5,
    )
    combined = GuardPolicy.objects.create(
        code="M",
        name="Max",
        mode=GuardMode.MAX_OF_FIXED_AND_PERCENT,
        fixed_left_hz=100_000,
        fixed_right_hz=100_000,
        percent_left=5,
        percent_right=5,
    )

    assert {fixed.mode, percent.mode, combined.mode} == set(GuardMode.values)


@pytest.mark.django_db
def test_guard_widths_cannot_be_negative():
    with pytest.raises(IntegrityError), transaction.atomic():
        GuardPolicy.objects.create(
            code="NEG",
            name="Negative",
            mode=GuardMode.FIXED,
            fixed_left_hz=-1,
            fixed_right_hz=0,
        )


# ---------------------------------------------------------------------------
# Polarization mappings
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_polarization_mapping_cannot_be_listed_twice():
    path = make_payload_path()
    PayloadPolarizationMapping.objects.create(
        payload_path=path,
        uplink_polarization=PolarizationType.RHCP,
        downlink_polarization=PolarizationType.LHCP,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        PayloadPolarizationMapping.objects.create(
            payload_path=path,
            uplink_polarization=PolarizationType.RHCP,
            downlink_polarization=PolarizationType.LHCP,
        )


@pytest.mark.django_db
def test_no_dependent_inventory_is_seeded():
    """Specification section 26.20.

    Frequency Windows (OQ-01), translations (OQ-02), polarization mappings (OQ-03) and
    guard values (OQ-07) are all unconfirmed. Shipping a plausible default would be
    indistinguishable from a confirmed one once loaded.
    """
    assert FrequencyWindow.objects.count() == 0
    assert PayloadPath.objects.count() == 0
    assert PayloadPolarizationMapping.objects.count() == 0
    assert GuardPolicy.objects.count() == 0
