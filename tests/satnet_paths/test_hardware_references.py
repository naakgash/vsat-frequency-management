"""GW ID and Decimator, which the answers took in opposite directions. **A-26**, **A-27**.

The register had these as one question — *"Is Decimator an exclusive hardware resource? Same as
OQ-09"* — and they are not the same at all:

* **OQ-09**: a GW ID is a *shared reference*. It becomes a controlled foreign key so naming and
  redundancy stay consistent, and it must **never** decide double-booking.
* **OQ-10**: a Decimator *is* allocatable, through a time-bounded assignment that no two
  overlapping configurations may share.

Treating them alike would have been wrong twice over — a false conflict on the Gateway and a
missed one on the Decimator — so both directions are pinned down here. The Decimator's own
constraint is in ``tests/inventory/test_decimator_assignments.py``; this file is about what the
Satnet Path does with the two references.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import models
from django.utils import timezone

from beams import services as beam_services
from satnet_paths import services
from satnet_paths.constants import InputMode, PathStatus
from satnet_paths.models import SatnetPath
from satnets import services as satnet_services
from spectrum.models import SpectrumReservation
from tests.factories import make_admin
from tests.inventory.factories import (
    make_decimator,
    make_decimator_assignment,
    make_gateway,
    make_hub,
)
from tests.spectrum.factories import make_entitlement

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


def _activate(setup, admin):
    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)


def _satnet(admin, beam, code, gateway):
    return satnet_services.create(
        actor=admin,
        values={
            "code": f"SN-{code}",
            "name": f"Satnet {code}",
            "beam": beam,
            "hub": make_hub(gateway, f"HUB-{code}"),
            "effective_from": timezone.now() - timezone.timedelta(days=1),
        },
    )


def _values(*, code, centre=50 * MHZ, **extra):
    values = {
        "code": code,
        "direction": "FWD",
        "status": PathStatus.PLANNED,
        "input_mode": InputMode.OCCUPIED_BW,
        "input_value": 10 * MHZ,
        "rolloff": Decimal("0.2"),
        "canonical_center_hz": centre,
        "valid_from": timezone.now(),
    }
    values.update(extra)
    return values


# ---------------------------------------------------------------------------
# OQ-09 — the Gateway is a reference, not a resource
# ---------------------------------------------------------------------------
def test_two_paths_through_one_gateway_do_not_conflict(world_of_two_beams):
    """**A-26**, the whole of it.

    Two allocations at the *same frequency*, at the *same time*, through the *same Gateway* —
    and they are both accepted, because their Beams' legs map to different spectrum resources.
    A platform that treated GW ID as exclusive would refuse the second, which is precisely what
    the answer forbids: *"double-booking shall not be determined from GW ID"*.
    """
    admin, first, second, gateway, _ = world_of_two_beams

    services.create(actor=admin, satnet=first, values=_values(code="GW-A", gateway=gateway))
    services.create(actor=admin, satnet=second, values=_values(code="GW-B", gateway=gateway))

    assert SatnetPath.objects.filter(gateway=gateway).count() == 2


def test_a_shared_resource_still_conflicts_across_different_gateways(world_of_shared_resource):
    """The other half of the same rule, and the reason the first test is not simply permissive.

    Different Beams, different Hubs, different Gateways — one shared payload input, and the
    second allocation is refused. Contention follows the resource, never the site.
    """
    admin, first, second, _, _ = world_of_shared_resource

    services.create(actor=admin, satnet=first, values=_values(code="SH-A"))

    with pytest.raises(services.PathBlockedError) as blocked:
        services.create(actor=admin, satnet=second, values=_values(code="SH-B"))

    assert "SPECTRUM_CONFLICT" in {finding.code for finding in blocked.value.findings}


def test_no_occupancy_row_carries_a_gateway():
    """The structural guard, so a later slice cannot quietly promote the reference.

    A test on behaviour alone would keep passing if somebody added a ``gateway`` column to the
    occupancy row and forgot to key on it — and then the day they *did* key on it, the failure
    would look like a spectrum bug. This asserts the shape: the reservation knows about
    resources, legs, polarizations and periods, and nothing about where the signal was
    transmitted from.
    """
    columns = {field.name for field in SpectrumReservation._meta.get_fields()}

    assert not columns & {"gateway", "hub", "site", "gw_id", "decimator"}


def test_the_exclusion_key_is_still_resource_rf_and_time():
    """**A-21**, asserted on the constraint itself rather than on its effects."""
    constraint = next(
        item
        for item in SpectrumReservation._meta.constraints
        if item.name == "excl_reservation_overlap"
    )

    assert [expression for expression, _ in constraint.expressions] == [
        "spectrum_resource",
        "allocated_rf",
        "active_period",
    ]


def test_the_gateway_is_a_controlled_reference_rather_than_free_text():
    """OQ-09: *"GW ID shall no longer be free text."*"""
    field = SatnetPath._meta.get_field("gateway")

    assert isinstance(field, models.ForeignKey)
    assert field.related_model._meta.label == "inventory.Gateway"
    assert field.verbose_name == "GW ID"


# ---------------------------------------------------------------------------
# OQ-10 — the Decimator is allocated through its assignment
# ---------------------------------------------------------------------------
def test_many_paths_may_consume_one_decimator_assignment(world_of_two_beams):
    """*"Multiple Satnet Paths may reference the same Decimator Assignment where they
    intentionally consume the same processed output and the payload supports fan-out, broadcast
    or multicast."*

    So the exclusivity is emphatically **not** on this foreign key. What is forbidden is two
    overlapping assignments on one Decimator, and that lives on the assignment table.
    """
    admin, first, second, _, hub = world_of_two_beams
    assignment = make_decimator_assignment(make_decimator(hub, "DEC-SHARED"))

    services.create(
        actor=admin, satnet=first, values=_values(code="DEC-A", decimator_assignment=assignment)
    )
    services.create(
        actor=admin, satnet=second, values=_values(code="DEC-B", decimator_assignment=assignment)
    )

    assert assignment.satnet_paths.count() == 2


def test_the_path_points_at_the_assignment_rather_than_the_box():
    """OQ-10: *"Satnet Paths shall reference the Decimator Assignment rather than storing a
    free-text Decimator value."* Pointing at the ``Decimator`` would lose the period, and the
    period is the whole of what makes it allocatable."""
    field = SatnetPath._meta.get_field("decimator_assignment")

    assert isinstance(field, models.ForeignKey)
    assert field.related_model._meta.label == "inventory.DecimatorAssignment"


def test_neither_reference_is_required():
    """Both stay optional. Which Paths have a Gateway or a Decimator recorded is site data, and
    demanding one would block allocations on information nobody has yet."""
    for name in ("gateway", "decimator_assignment"):
        assert SatnetPath._meta.get_field(name).null


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def world_of_two_beams():
    """Two Satnets whose Beams compete on nothing, both reached through one Gateway."""
    admin = make_admin()
    gateway = make_gateway("GW-ONE")

    first = make_entitlement(code="IND1", start_hz=0, end_hz=100 * MHZ)
    second = make_entitlement(code="IND2", start_hz=0, end_hz=100 * MHZ)
    _activate(first, admin)
    _activate(second, admin)

    satnet_a = _satnet(admin, first.beam, "IND1", gateway)
    satnet_b = _satnet(admin, second.beam, "IND2", gateway)
    return admin, satnet_a, satnet_b, gateway, satnet_a.hub


@pytest.fixture
def world_of_shared_resource():
    """Two Satnets at two Gateways whose Beams share one payload input."""
    admin = make_admin()

    first = make_entitlement(code="SHR1", start_hz=0, end_hz=100 * MHZ)
    # Sharing the resource is what ``make_entitlement(resource=...)`` exists for: it repoints
    # the second Beam's hub-uplink mapping at the first Beam's resource, so the two legs
    # genuinely compete rather than merely looking as though they might.
    second = make_entitlement(
        code="SHR2",
        satellite=first.satellite,
        resource=first.resource,
        start_hz=0,
        end_hz=100 * MHZ,
    )
    _activate(first, admin)
    _activate(second, admin)

    satnet_a = _satnet(admin, first.beam, "SHR1", make_gateway("GW-SHR-A"))
    satnet_b = _satnet(admin, second.beam, "SHR2", make_gateway("GW-SHR-B"))
    return admin, satnet_a, satnet_b, None, None
