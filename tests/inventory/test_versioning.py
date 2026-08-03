"""Master-data versioning.

Specification section 13.6 — *"A Window in operational use is changed through versioning,
not retroactive overwrite"* — and design assumption **A-16**.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent
from inventory import services, versioning
from inventory.models import FrequencyWindow, PayloadPath
from tests.factories import make_admin
from tests.inventory.factories import make_frequency_window, make_payload_path, make_satellite


@pytest.mark.django_db
def test_a_new_record_starts_at_version_one_in_its_own_group():
    window = make_frequency_window()

    assert window.version_number == 1
    assert window.supersedes is None
    assert window.version_group is not None


@pytest.mark.django_db
def test_superseding_closes_the_predecessor_and_opens_a_successor():
    admin = make_admin()
    window = make_frequency_window(rf_start_hz=29_000_000_000, rf_end_hz=29_500_000_000)
    changeover = timezone.now() + timedelta(days=1)

    successor = versioning.supersede(
        actor=admin,
        instance=window,
        values={"rf_end_hz": 29_600_000_000},
        effective_from=changeover,
        reason="Transponder re-plan",
    )

    window.refresh_from_db()
    assert window.effective_until == changeover
    assert successor.version_number == 2
    assert successor.version_group == window.version_group
    assert successor.supersedes_id == window.pk
    assert successor.effective_until is None
    # Unchanged values carry forward; the changed one does not.
    assert successor.rf_start_hz == 29_000_000_000
    assert successor.rf_end_hz == 29_600_000_000


@pytest.mark.django_db
def test_two_active_versions_of_one_group_are_refused_by_the_database():
    """The constraint that makes versioning meaningful.

    With two active versions an allocation would have two different definitions of what it
    must fit inside, and nothing would say which one wins.
    """
    window = make_frequency_window()

    with pytest.raises(IntegrityError, match="excl_window_version_overlap"), transaction.atomic():
        FrequencyWindow.objects.create(
            code=window.code + "-B",
            name="Overlapping second version",
            satellite=window.satellite,
            band=window.band,
            side=window.side,
            polarization=window.polarization,
            rf_start_hz=window.rf_start_hz,
            rf_end_hz=window.rf_end_hz,
            effective_from=window.effective_from,
            # Same group, overlapping period, both active.
            version_group=window.version_group,
            version_number=2,
        )


@pytest.mark.django_db
def test_consecutive_versions_with_touching_periods_are_allowed():
    """Half-open periods make handover exact: the successor starts where the predecessor
    ends, and the two do not overlap (**A-10**)."""
    admin = make_admin()
    window = make_frequency_window()
    changeover = timezone.now() + timedelta(days=1)

    successor = versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=changeover
    )

    window.refresh_from_db()
    assert window.effective_until == successor.effective_from
    assert FrequencyWindow.objects.filter(version_group=window.version_group).count() == 2


@pytest.mark.django_db
def test_superseding_an_already_superseded_version_is_refused():
    admin = make_admin()
    window = make_frequency_window()
    versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    window.refresh_from_db()

    with pytest.raises(ValueError, match="already been superseded"):
        versioning.supersede(
            actor=admin,
            instance=window,
            values={},
            effective_from=timezone.now() + timedelta(days=2),
        )


@pytest.mark.django_db
def test_a_successor_cannot_start_before_its_predecessor():
    admin = make_admin()
    window = make_frequency_window()

    with pytest.raises(ValueError, match="must take effect after"):
        versioning.supersede(
            actor=admin,
            instance=window,
            values={},
            effective_from=window.effective_from - timedelta(days=1),
        )


@pytest.mark.django_db
def test_superseding_is_audited_with_before_and_after():
    admin = make_admin()
    window = make_frequency_window(rf_end_hz=29_500_000_000)

    versioning.supersede(
        actor=admin,
        instance=window,
        values={"rf_end_hz": 29_600_000_000},
        effective_from=timezone.now() + timedelta(days=1),
        reason="Transponder re-plan",
    )

    event = AuditEvent.objects.get(action="MASTER_DATA_VERSIONED")
    assert event.actor_id == admin.pk
    assert event.before["rf_end_hz"] == 29_500_000_000
    assert event.after["rf_end_hz"] == 29_600_000_000
    assert event.change_reason == "Transponder re-plan"


# ---------------------------------------------------------------------------
# Retroactive overwrite
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_engineering_values_of_an_unused_window_may_be_edited_in_place():
    """Versioning protects operational data. A window nothing references yet is still
    being drafted, and forcing a version for every correction would be noise."""
    admin = make_admin()
    window = make_frequency_window(rf_end_hz=29_500_000_000)

    services.update(
        actor=admin,
        instance=window,
        values={"rf_end_hz": 29_700_000_000},
        expected_version=window.record_version,
    )

    window.refresh_from_db()
    assert window.rf_end_hz == 29_700_000_000
    assert window.version_number == 1


@pytest.mark.django_db
def test_engineering_values_of_a_referenced_window_cannot_be_edited_in_place():
    """Specification section 13.6, the rule this slice exists for."""
    admin = make_admin()
    path = make_payload_path()
    window = path.uplink_window

    with pytest.raises(versioning.RetroactiveEditError) as excinfo:
        services.update(
            actor=admin,
            instance=window,
            values={"rf_end_hz": window.rf_end_hz + 1_000_000},
            expected_version=window.record_version,
        )

    assert "1 Payload Paths" in str(excinfo.value)
    assert "create a new version" in str(excinfo.value)
    window.refresh_from_db()
    assert window.rf_end_hz != window.rf_end_hz + 1_000_000


@pytest.mark.django_db
def test_a_referenced_window_may_still_have_its_description_corrected():
    """Refusing to fix a typo would push people towards editing the database directly,
    which is worse than the thing the rule protects against."""
    admin = make_admin()
    path = make_payload_path()
    window = path.uplink_window

    services.update(
        actor=admin,
        instance=window,
        values={"description": "Corrected wording"},
        expected_version=window.record_version,
    )

    window.refresh_from_db()
    assert window.description == "Corrected wording"


@pytest.mark.django_db
def test_a_referenced_window_is_changed_by_superseding_it():
    """The supported route: the old definition stays intact for existing allocations."""
    admin = make_admin()
    path = make_payload_path()
    window = path.uplink_window
    original_end = window.rf_end_hz

    successor = versioning.supersede(
        actor=admin,
        instance=window,
        values={"rf_end_hz": original_end + 1_000_000},
        effective_from=timezone.now() + timedelta(days=1),
        reason="Widened after coordination",
    )

    window.refresh_from_db()
    # The payload path still points at the definition it was validated against.
    path.refresh_from_db()
    assert path.uplink_window_id == window.pk
    assert window.rf_end_hz == original_end
    assert successor.rf_end_hz == original_end + 1_000_000


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_version_history_returns_the_whole_chain_oldest_first():
    admin = make_admin()
    window = make_frequency_window()
    second = versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    third = versioning.supersede(
        actor=admin, instance=second, values={}, effective_from=timezone.now() + timedelta(days=2)
    )

    history = versioning.version_history(third)

    assert [v.version_number for v in history] == [1, 2, 3]


@pytest.mark.django_db
def test_current_version_is_the_open_ended_active_one():
    admin = make_admin()
    window = make_frequency_window()
    successor = versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )

    assert versioning.current_version(window) == successor


@pytest.mark.django_db
def test_equipment_profiles_are_versioned_on_the_same_machinery():
    """A-16 applies the rule to all three engineering-critical entities."""
    from tests.inventory.factories import make_equipment_profile

    admin = make_admin()
    profile = make_equipment_profile(lo_hz=28_050_000_000)

    successor = versioning.supersede(
        actor=admin,
        instance=profile,
        values={"lo_hz": 28_100_000_000},
        effective_from=timezone.now() + timedelta(days=1),
    )

    assert successor.version_number == 2
    assert successor.lo_hz == 28_100_000_000
    profile.refresh_from_db()
    assert profile.lo_hz == 28_050_000_000


@pytest.mark.django_db
def test_payload_paths_are_versioned_too():
    admin = make_admin()
    path = make_payload_path(translation_constant_hz=1_000_000_000)

    successor = versioning.supersede(
        actor=admin,
        instance=path,
        values={"translation_constant_hz": 1_500_000_000},
        effective_from=timezone.now() + timedelta(days=1),
    )

    assert successor.version_number == 2
    assert PayloadPath.objects.filter(version_group=path.version_group).count() == 2


@pytest.mark.django_db
def test_a_satellite_reports_its_windows_and_paths():
    """Dependency summaries reach the new entities (section 3.2)."""
    from inventory import dependencies

    satellite = make_satellite("SAT-D")
    make_payload_path(satellite=satellite)

    summary = {d.label: d.count for d in dependencies.summarise(satellite)}

    assert summary["Frequency Windows"] == 2
    assert summary["Payload Paths"] == 1
