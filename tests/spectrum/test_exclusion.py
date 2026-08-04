"""The promise: two allocations cannot occupy the same Hz on the same resource. §8.1, §8.3.

Everything up to S9 *described* spectrum. This is the slice where the platform guarantees it,
and these tests are the guarantee.

They are written against the **database**, not the service layer, on purpose. §8.3: *"Keep
PostgreSQL as the final defense layer… they must not be the only protection."* A service test
proves the service is careful. Only a test that writes rows directly proves that carelessness
elsewhere — an importer, a migration, a psql session — cannot get past it.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from spectrum.constants import ReservationKind, ReservationStatus
from spectrum.models import SpectrumReservation
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db


def test_two_allocations_on_one_resource_cannot_overlap():
    """The whole point of the product, in six lines."""
    setup = make_entitlement()
    reserve_range(setup, 100_000_000, 110_000_000)

    with pytest.raises(IntegrityError, match="excl_reservation_overlap"):
        with transaction.atomic():
            reserve_range(setup, 105_000_000, 115_000_000)


def test_the_constraint_compares_allocated_not_occupied():
    """§8.1: the reserved interval **includes guard bands**.

    Two transmissions whose occupied ranges are 2 MHz apart but whose guards meet in the
    middle are in conflict. Comparing the occupied ranges would accept them, and the guards
    would exist only as decoration.
    """
    setup = make_entitlement()
    reserve_range(setup, 100_000_000, 110_000_000, guard_hz=2_000_000)

    with pytest.raises(IntegrityError, match="excl_reservation_overlap"):
        with transaction.atomic():
            # Occupied 111-120 does not overlap occupied 100-110. Allocated 109-122 does
            # overlap allocated 98-112.
            reserve_range(setup, 111_000_000, 120_000_000, guard_hz=2_000_000)


def test_touching_allocations_are_accepted():
    """**A-11**: ranges are half-open, so adjacency is not overlap.

    Any required physical separation is a guard band and must be stated as one. This is the
    behaviour that makes `[…,100)` next to `[100,…)` arithmetic rather than a special case.
    """
    setup = make_entitlement()
    reserve_range(setup, 100_000_000, 110_000_000)

    second = reserve_range(setup, 110_000_000, 120_000_000)

    assert second.pk is not None


def test_the_same_frequency_on_two_resources_is_permitted():
    """Frequency reuse, which is the case **A-01** got wrong (ADR-0018).

    Two Beams whose legs are independent RF chains are two resources, and identical spectrum
    on both is correct rather than a conflict.
    """
    first = make_entitlement(code="A")
    second = make_entitlement(code="B", satellite=first.satellite)

    reserve_range(first, 100_000_000, 110_000_000)
    other = reserve_range(second, 100_000_000, 110_000_000)

    assert other.pk is not None
    assert SpectrumReservation.objects.count() == 2


def test_the_same_frequency_on_a_shared_resource_is_refused():
    """The other half, and the reason the OQ-25 answer mattered.

    Two *different Beams* sharing one payload input now collide — which the superseded
    Beam-keyed constraint would have accepted, silently.
    """
    first = make_entitlement(code="A")
    second = make_entitlement(code="B", satellite=first.satellite, resource=first.resource)

    reserve_range(first, 100_000_000, 110_000_000)

    with pytest.raises(IntegrityError, match="excl_reservation_overlap"):
        with transaction.atomic():
            reserve_range(second, 105_000_000, 108_000_000)


def test_overlapping_rf_in_non_overlapping_periods_is_permitted():
    """Time is half of the key. A retired allocation's spectrum is free again."""
    setup = make_entitlement()
    now = timezone.now()
    reserve_range(setup, 100_000_000, 110_000_000, valid_until=now + timezone.timedelta(days=30))

    later = reserve_range(
        setup,
        100_000_000,
        110_000_000,
        valid_from=now + timezone.timedelta(days=30),
    )

    assert later.pk is not None


def test_a_status_that_does_not_reserve_does_not_block():
    """`WHERE (reserves_spectrum)`. A draft holds nothing.

    ``reserves=False`` is not optional here: `ck_res_reserves_status` refuses a `DRAFT` row
    that claims to hold spectrum (**A-12**), so the two columns cannot be set independently.
    Writing this test the obvious way was itself caught by the constraint.
    """
    setup = make_entitlement()
    reserve_range(setup, 100_000_000, 110_000_000, status=ReservationStatus.DRAFT, reserves=False)

    live = reserve_range(setup, 105_000_000, 108_000_000)

    assert live.pk is not None


def test_a_fixed_reserve_and_an_allocation_exclude_each_other():
    """**A-13**, and the whole reason they share a table.

    §16 subtracts fixed reserve areas from free spectrum. An exclusion constraint cannot span
    two tables, so a fixed reserve in its own table could be overlapped freely.
    """
    setup = make_entitlement()
    reserve_range(
        setup,
        100_000_000,
        110_000_000,
        kind=ReservationKind.FIXED_RESERVE,
        satnet_path_id=None,
        status="",
        direction="",
    )

    with pytest.raises(IntegrityError, match="excl_reservation_overlap"):
        with transaction.atomic():
            reserve_range(setup, 105_000_000, 108_000_000)


# ---------------------------------------------------------------------------
# The CHECKs of §20
# ---------------------------------------------------------------------------
def test_occupied_must_sit_inside_allocated():
    setup = make_entitlement()

    with pytest.raises(IntegrityError, match="ck_res_occ_in_alloc"):
        with transaction.atomic():
            reserve_range(setup, 100_000_000, 110_000_000, occupied_overhang_hz=1)


def test_an_allocation_cannot_escape_its_assignment():
    """ADR-0019. **Allocated**, not occupied — guards are part of what is reserved, and
    spectrum outside the entitlement belongs to another Beam."""
    setup = make_entitlement(start_hz=100_000_000, end_hz=200_000_000)

    with pytest.raises(IntegrityError, match="ck_res_within_assignment"):
        with transaction.atomic():
            reserve_range(setup, 195_000_000, 199_000_000, guard_hz=5_000_000)


def test_the_assignment_bounds_copy_cannot_lie():
    """`fk_reservation_assignment_bounds`.

    Without it, widening the copy satisfies `ck_res_within_assignment` and lets a reservation
    hold spectrum the Beam is not entitled to — on a resource it may share.
    """
    setup = make_entitlement()
    row = reserve_range(setup, 100_000_000, 110_000_000)

    with pytest.raises(IntegrityError, match="fk_reservation_assignment_bounds"):
        with transaction.atomic():
            row.assignment_end_hz += 500_000_000
            row.save()


def test_a_reserving_status_cannot_claim_not_to_reserve():
    """**A-12**. Every status whose policy is fixed is pinned by the database."""
    setup = make_entitlement()

    with pytest.raises(IntegrityError, match="ck_res_reserves_status"):
        with transaction.atomic():
            reserve_range(
                setup, 100_000_000, 110_000_000, status=ReservationStatus.ON_AIR, reserves=False
            )


def test_suspended_may_go_either_way():
    """**OQ-08** is a runtime setting, so the CHECK deliberately does not pin it.

    Pinning it would answer an open question by implication, which is the failure mode the
    whole register exists to prevent.
    """
    setup = make_entitlement()

    holding = reserve_range(
        setup, 100_000_000, 110_000_000, status=ReservationStatus.SUSPENDED, reserves=True
    )
    released = reserve_range(
        setup, 200_000_000, 210_000_000, status=ReservationStatus.SUSPENDED, reserves=False
    )

    assert holding.pk is not None and released.pk is not None


def test_a_forward_allocation_cannot_claim_a_return_leg():
    """§20 side consistency, **A-03**."""
    setup = make_entitlement()

    with pytest.raises(IntegrityError, match="ck_res_direction_leg"):
        with transaction.atomic():
            reserve_range(setup, 100_000_000, 110_000_000, leg="REMOTE_UPLINK")


def test_a_fixed_reserve_may_not_name_a_satnet_path():
    setup = make_entitlement()

    with pytest.raises(IntegrityError, match="ck_res_kind_path"):
        with transaction.atomic():
            reserve_range(
                setup, 100_000_000, 110_000_000, kind=ReservationKind.FIXED_RESERVE, status=""
            )


def test_a_satnet_path_reservation_must_name_its_path():
    setup = make_entitlement()

    with pytest.raises(IntegrityError, match="ck_res_kind_path"):
        with transaction.atomic():
            reserve_range(setup, 100_000_000, 110_000_000, satnet_path_id=None)


# ---------------------------------------------------------------------------
# No write route exists (§13.11)
# ---------------------------------------------------------------------------
def test_no_role_holds_add_change_or_delete_on_a_reservation():
    """§13.11: there is no write route to this table for any role.

    Asserted against the permissions Django would have generated, because
    `default_permissions = ("view",)` is one line and deleting it would silently create three
    permissions that somebody could then grant.
    """
    from django.contrib.auth.models import Permission

    codenames = set(
        Permission.objects.filter(
            content_type__app_label="spectrum", content_type__model="spectrumreservation"
        ).values_list("codename", flat=True)
    )

    assert codenames == {"view_spectrumreservation"}
