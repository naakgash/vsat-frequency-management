"""Writing reservations through the only route there is. §13.11, §15.6.

The database tests prove the constraint. These prove the service writes rows the constraint
will accept, writes **all** of them or none, and refuses the containment case the database
cannot hold.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from calculations.ranges import FrequencyRange
from inventory.constants import SpectrumResourceKind
from inventory.models import SpectrumResource
from spectrum import services
from spectrum.constants import ReservationStatus
from spectrum.models import SpectrumReservation
from tests.factories import make_admin
from tests.spectrum.factories import make_entitlement

pytestmark = pytest.mark.django_db


def _occupancy(setup, *, start=100_000_000, end=110_000_000, guard=1_000_000, resources=None):
    return services.Occupancy(
        assignment=setup.assignment,
        leg="HUB_UPLINK",
        polarization="RHCP",
        occupied=FrequencyRange(start, end),
        allocated=FrequencyRange(start - guard, end + guard),
        resource_ids=resources or (str(setup.resource.pk),),
    )


def _admin():
    """One admin per test, however many times a test reserves.

    ``make_admin()`` creates a fixed username, so calling it twice in one test collides on
    the unique index. Reusing the row is right anyway — a test that reserves twice is one
    operator doing two things, not two operators.
    """
    from accounts.models import User

    return User.objects.filter(username="an-admin").first() or make_admin()


def _reserve(setup, occupancies, **extra):
    return services.reserve(
        actor=_admin(),
        occupancies=occupancies,
        satnet_path_id=extra.pop("satnet_path_id", uuid.uuid4()),
        direction="FWD",
        status=extra.pop("status", ReservationStatus.PLANNED),
        valid_from=extra.pop("valid_from", timezone.now()),
        **extra,
    )


def test_one_occupancy_on_one_resource_writes_one_row():
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)

    rows = _reserve(setup, [_occupancy(setup)])

    assert len(rows) == 1
    assert rows[0].allocated_start_hz == 99_000_000


def test_one_leg_on_several_resources_writes_a_row_each():
    """**A-23**: *"an allocation may reserve more than one spectrum resource."*

    Not two rows per allocation — one per resource per leg. Anything that assumes a
    canonical/translated pair breaks the first time a leg shares two chains.
    """
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    second = SpectrumResource.objects.create(
        satellite=setup.satellite,
        code="SR-SECOND",
        name="Second chain",
        kind=SpectrumResourceKind.RF_CHAIN,
        leg="HUB_UPLINK",
        effective_from="2026-01-01T00:00:00Z",
    )

    rows = _reserve(
        setup,
        [_occupancy(setup, resources=(str(setup.resource.pk), str(second.pk)))],
    )

    assert len(rows) == 2
    assert {str(row.spectrum_resource_id) for row in rows} == {
        str(setup.resource.pk),
        str(second.pk),
    }


def test_a_conflict_on_the_second_resource_rolls_back_the_first():
    """All or nothing (§15.6).

    A half-written allocation is worse than a refused one: the committed rows would hold
    spectrum for a Satnet Path that does not exist, and nothing would ever release them.
    """
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    contended = SpectrumResource.objects.create(
        satellite=setup.satellite,
        code="SR-BUSY",
        name="Busy chain",
        kind=SpectrumResourceKind.RF_CHAIN,
        leg="HUB_UPLINK",
        effective_from="2026-01-01T00:00:00Z",
    )
    SpectrumReservation.objects.create(
        spectrum_resource=contended,
        beam_spectrum_assignment=setup.assignment,
        assignment_start_hz=setup.assignment.rf_start_hz,
        assignment_end_hz=setup.assignment.rf_end_hz,
        leg="HUB_UPLINK",
        polarization="RHCP",
        occupied_start_hz=100_000_000,
        occupied_end_hz=110_000_000,
        allocated_start_hz=100_000_000,
        allocated_end_hz=110_000_000,
        valid_from=timezone.now(),
        kind="SATNET_PATH",
        satnet_path_id=uuid.uuid4(),
        direction="FWD",
        status=ReservationStatus.ON_AIR,
    )
    before = SpectrumReservation.objects.count()

    with pytest.raises(services.SpectrumConflictError):
        _reserve(
            setup,
            [_occupancy(setup, resources=(str(setup.resource.pk), str(contended.pk)))],
        )

    assert SpectrumReservation.objects.count() == before, (
        "the row on the free resource must not survive the conflict on the busy one"
    )


def test_reserves_spectrum_is_derived_from_the_status():
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)

    planned = _reserve(setup, [_occupancy(setup)], status=ReservationStatus.PLANNED)
    draft = _reserve(
        setup,
        [_occupancy(setup, start=300_000_000, end=310_000_000)],
        status=ReservationStatus.DRAFT,
    )

    assert planned[0].reserves_spectrum is True
    assert draft[0].reserves_spectrum is False


def test_a_suspended_allocation_follows_the_setting():
    """**OQ-08**. The one status whose answer is configuration rather than policy."""
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)

    retained = _reserve(
        setup,
        [_occupancy(setup)],
        status=ReservationStatus.SUSPENDED,
        suspended_retains=True,
    )
    released = _reserve(
        setup,
        [_occupancy(setup, start=300_000_000, end=310_000_000)],
        status=ReservationStatus.SUSPENDED,
        suspended_retains=False,
    )

    assert retained[0].reserves_spectrum is True
    assert released[0].reserves_spectrum is False


def test_an_allocation_outside_its_assignment_period_is_refused():
    """The half of containment the database cannot hold.

    An open-ended assignment has ``effective_until IS NULL``, and a MATCH SIMPLE composite
    foreign key with a NULL in any column is satisfied trivially — so the constraint would be
    vacuous in the common case. The service checks it, and the refusal names the assignment.
    """
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    setup.assignment.effective_until = timezone.now() + timezone.timedelta(days=10)
    setup.assignment.save()

    with pytest.raises(services.OutsideEntitlementError) as excinfo:
        _reserve(setup, [_occupancy(setup)], valid_until=None)

    assert excinfo.value.assignment == setup.assignment
    assert "past the end" in str(excinfo.value)


def test_an_allocation_starting_before_its_assignment_is_refused():
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)

    with pytest.raises(services.OutsideEntitlementError, match="before its spectrum assignment"):
        _reserve(
            setup,
            [_occupancy(setup)],
            valid_from=setup.assignment.effective_from - timezone.timedelta(days=1),
        )


def test_an_inactive_assignment_holds_nothing():
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    setup.assignment.is_active = False
    setup.assignment.save()

    with pytest.raises(services.OutsideEntitlementError, match="not active"):
        _reserve(setup, [_occupancy(setup)])


def test_reserving_is_audited_with_the_resources_it_touched():
    """§18. "Spectrum was reserved" is not enough — the trail has to say *where*."""
    from audit.models import AuditEvent

    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    _reserve(setup, [_occupancy(setup)], reason="Initial allocation")

    event = AuditEvent.objects.get(action="SPECTRUM_RESERVED")
    assert event.after["rows"] == 1
    assert str(setup.resource.pk) in event.after["resources"]
    assert event.change_reason == "Initial allocation"


def test_releasing_removes_every_row_of_one_allocation_and_is_audited():
    from audit.models import AuditEvent

    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    path_id = uuid.uuid4()
    _reserve(setup, [_occupancy(setup)], satnet_path_id=path_id)

    released = services.release(actor=_admin(), satnet_path_id=path_id, reason="Cancelled")

    assert released == 1
    assert SpectrumReservation.objects.filter(satnet_path_id=path_id).count() == 0
    assert AuditEvent.objects.filter(action="SPECTRUM_RELEASED").exists()


def test_releasing_frees_the_spectrum_for_someone_else():
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    path_id = uuid.uuid4()
    _reserve(setup, [_occupancy(setup)], satnet_path_id=path_id)
    services.release(actor=_admin(), satnet_path_id=path_id)

    rows = _reserve(setup, [_occupancy(setup)])

    assert len(rows) == 1


def test_a_committed_overlap_is_raised_as_a_spectrum_conflict():
    """The service translates the constraint's error into one an operator can be shown.

    §9.5 needs a refusal that names the problem. An `IntegrityError` carrying a constraint
    name is a database fact, not a message, and the caller should not have to grep it.
    """
    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    _reserve(setup, [_occupancy(setup)])

    with pytest.raises(services.SpectrumConflictError) as excinfo:
        _reserve(setup, [_occupancy(setup, start=105_000_000, end=108_000_000)])

    assert excinfo.value.was_deadlock is False
    assert "already reserved" in str(excinfo.value)


def test_an_unrelated_integrity_error_is_not_disguised_as_a_conflict():
    """The translation is narrow on purpose.

    Catching every `IntegrityError` and calling it a spectrum conflict would report a broken
    foreign key or a violated CHECK as "this spectrum is taken" — which sends whoever reads it
    looking for a competing allocation that does not exist.
    """
    from django.db import IntegrityError

    setup = make_entitlement(start_hz=0, end_hz=1_000_000_000)
    outside = services.Occupancy(
        assignment=setup.assignment,
        leg="HUB_UPLINK",
        polarization="RHCP",
        occupied=FrequencyRange(2_000_000_000, 2_010_000_000),
        allocated=FrequencyRange(2_000_000_000, 2_010_000_000),
        resource_ids=(str(setup.resource.pk),),
    )

    with pytest.raises(IntegrityError) as excinfo:
        _reserve(setup, [outside])

    assert not isinstance(excinfo.value, services.SpectrumConflictError)
    assert "ck_res_within_assignment" in str(excinfo.value)
