"""Free capacity through the ORM, and who may see it. §16, §25, ADR-0009.

``tests/domain/test_gaps.py`` proves the arithmetic. This proves the *selector* hands it the
right rows — which is where the correctness risk actually sits, because the engine will
faithfully compute gaps in whatever entitlement it is given.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.constants import Role
from beams.models import BeamSpectrumAssignment
from spectrum import selectors
from spectrum.constants import ReservationStatus
from tests.factories import make_admin, make_user
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db

LEG = "HUB_UPLINK"


def test_an_untouched_entitlement_is_entirely_free():
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)

    summary = selectors.capacity(setup.config, leg=LEG)

    assert summary.total_hz == 100_000_000
    assert summary.free_hz == 100_000_000


def test_a_reservation_reduces_free_capacity_by_its_allocated_width():
    """Allocated, not occupied: guards are held spectrum (§8.1)."""
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    reserve_range(setup, 40_000_000, 50_000_000, guard_hz=1_000_000)

    summary = selectors.capacity(setup.config, leg=LEG)

    assert summary.used_hz == 12_000_000
    assert summary.free_hz == 88_000_000


def test_another_beams_reservation_on_a_shared_resource_counts_against_this_one():
    """The OQ-25 answer, visible on a screen.

    Under the superseded A-01 this Beam would have been told the spectrum was free. It is not
    — another Beam is transmitting on the same payload input.
    """
    first = make_entitlement(code="A", start_hz=0, end_hz=100_000_000)
    second = make_entitlement(
        code="B", satellite=first.satellite, resource=first.resource, start_hz=0, end_hz=100_000_000
    )
    reserve_range(first, 40_000_000, 50_000_000)

    summary = selectors.capacity(second.config, leg=LEG)

    assert summary.used_hz == 10_000_000
    assert any(gap.range.end_hz == 40_000_000 for gap in summary.gaps)


def test_a_reservation_on_a_different_resource_does_not_count():
    """The other half. Independent chains are independent capacity."""
    first = make_entitlement(code="A", start_hz=0, end_hz=100_000_000)
    second = make_entitlement(code="B", satellite=first.satellite, start_hz=0, end_hz=100_000_000)
    reserve_range(first, 40_000_000, 50_000_000)

    summary = selectors.capacity(second.config, leg=LEG)

    assert summary.free_hz == 100_000_000


def test_a_status_that_holds_nothing_does_not_reduce_capacity():
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    reserve_range(setup, 40_000_000, 50_000_000, status=ReservationStatus.DRAFT, reserves=False)

    assert selectors.capacity(setup.config, leg=LEG).free_hz == 100_000_000


def test_an_expired_reservation_frees_its_spectrum():
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    past = timezone.now() - timezone.timedelta(days=10)
    reserve_range(
        setup,
        40_000_000,
        50_000_000,
        valid_from=past,
        valid_until=timezone.now() - timezone.timedelta(days=1),
    )

    assert selectors.capacity(setup.config, leg=LEG).free_hz == 100_000_000


def test_capacity_is_bounded_by_the_assignment_not_the_window():
    """ADR-0019, and the sentence in the OQ-27 answer this exists to satisfy.

    The window is 100 MHz. The Beam is entitled to 20 MHz of it. Its capacity is 20 MHz, and
    the other 80 MHz is not free — it is somebody else's.
    """
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    assignment = setup.assignment
    assignment.rf_end_hz = 20_000_000
    assignment.save()

    summary = selectors.capacity(setup.config, leg=LEG)

    assert summary.total_hz == 20_000_000
    assert summary.free_hz == 20_000_000
    assert all(gap.range.end_hz <= 20_000_000 for gap in summary.gaps)


def test_two_assignments_give_two_separate_pools():
    """**OQ-27**: one or more sub-ranges, and a gap is never merged across two of them."""
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    first = setup.assignment
    first.rf_end_hz = 20_000_000
    first.save()
    BeamSpectrumAssignment.objects.create(
        direction_config=setup.config,
        frequency_window=setup.config.uplink_window,
        payload_path=setup.config.payload_path,
        rf_start_hz=60_000_000,
        rf_end_hz=80_000_000,
        window_rf_start_hz=0,
        window_rf_end_hz=100_000_000,
        effective_from=setup.assignment.effective_from,
    )

    summary = selectors.capacity(setup.config, leg=LEG)

    assert summary.total_hz == 40_000_000
    assert [(g.range.start_hz, g.range.end_hz) for g in summary.gaps] == [
        (0, 20_000_000),
        (60_000_000, 80_000_000),
    ]


def test_an_expired_assignment_entitles_the_beam_to_nothing():
    """Not "the whole window". A direction whose entitlement lapsed may use none of it, and
    saying otherwise would offer spectrum on the strength of a lapsed grant."""
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    # Both ends move: `effective_period` is a generated tstzrange, and PostgreSQL refuses a
    # lower bound above its upper one before any application rule gets a look in.
    setup.assignment.effective_from = timezone.now() - timezone.timedelta(days=30)
    setup.assignment.effective_until = timezone.now() - timezone.timedelta(days=1)
    setup.assignment.save()

    summary = selectors.capacity(setup.config, leg=LEG)

    assert summary.total_hz == 0
    assert summary.gaps == ()


def test_an_allocation_being_revised_does_not_compete_with_itself():
    """§15.4. Without the exclusion an operator editing a live path is told their own
    transmission is in the way."""
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    row = reserve_range(setup, 40_000_000, 50_000_000)

    summary = selectors.capacity(setup.config, leg=LEG, exclude_satnet_path_id=row.satnet_path_id)

    assert summary.free_hz == 100_000_000


# ---------------------------------------------------------------------------
# Who may look
# ---------------------------------------------------------------------------
def test_the_spectrum_view_needs_sign_in(client):
    setup = make_entitlement()

    response = client.get(reverse("spectrum:beam", kwargs={"pk": setup.beam.pk}))

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER])
def test_every_role_may_read_the_spectrum_view(client, role):
    """An Operator choosing where to put a transmission has to be able to see what is
    already there."""
    setup = make_entitlement()
    user = make_user(f"user-{role}", roles=[role])
    client.force_login(user)

    response = client.get(reverse("spectrum:beam", kwargs={"pk": setup.beam.pk}))

    assert response.status_code == 200


def test_the_view_reports_capacity_and_held_spectrum(client):
    setup = make_entitlement(start_hz=0, end_hz=100_000_000)
    reserve_range(setup, 40_000_000, 50_000_000)
    client.force_login(make_admin())

    response = client.get(reverse("spectrum:beam", kwargs={"pk": setup.beam.pk}))

    panels = response.context["panels"]
    uplink = next(p for p in panels if p["leg"] == LEG)
    assert uplink["summary"].used_hz == 10_000_000
    assert len(uplink["reservations"]) == 1
    assert len(uplink["bars"]) == 1
