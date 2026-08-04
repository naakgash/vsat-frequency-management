"""Satnet lifecycle, containment and computed capacity. §6, §13.9, §16, §26.8."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.constants import Role
from accounts.models import UserBeamScope, UserHubScope
from satnets import selectors, services
from satnets.models import Satnet
from tests.factories import make_admin, make_user
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db


@pytest.fixture
def satnet():
    """A Satnet on an active Beam whose FWD direction is fully configured."""
    setup = make_entitlement(code="LC", start_hz=0, end_hz=100_000_000)
    admin = make_admin()
    from beams import services as beam_services

    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    gateway = make_gateway("GW-LC")
    hub = make_hub(gateway, "HUB-LC")
    record = services.create(
        actor=admin,
        values={
            "code": "SN-LC",
            "name": "Lifecycle",
            "beam": setup.beam,
            "hub": hub,
            "effective_from": timezone.now(),
        },
    )
    return {"satnet": record, "setup": setup, "admin": admin, "hub": hub}


# ---------------------------------------------------------------------------
# Shape and constraints
# ---------------------------------------------------------------------------
def test_the_gateway_is_derived_from_the_hub_and_never_submitted(satnet):
    """A form could otherwise post a Gateway the Hub is not at. The composite key would
    refuse it, but by then the error is a constraint name rather than a field."""
    assert satnet["satnet"].gateway_id == satnet["hub"].gateway_id


def test_a_satnet_cannot_claim_a_gateway_its_hub_is_not_at(satnet):
    """`fk_satnet_hub_gateway`. The denormalised column is only worth having if it cannot lie
    — a scope check that trusted a wrong copy would answer about the wrong site, in the
    direction that grants access."""
    record = satnet["satnet"]
    elsewhere = make_gateway("GW-ELSEWHERE")

    with pytest.raises(IntegrityError, match="fk_satnet_hub_gateway"):
        with transaction.atomic():
            record.gateway = elsewhere
            record.save()


def test_two_satnets_on_one_beam_cannot_share_a_code(satnet):
    """**A-18**: Satnet codes are unique per Beam."""
    with pytest.raises(IntegrityError, match="uq_satnet_beam_code"):
        with transaction.atomic():
            Satnet.objects.create(
                code="SN-LC",
                name="Duplicate",
                beam=satnet["setup"].beam,
                hub=satnet["hub"],
                gateway=satnet["hub"].gateway,
                effective_from=timezone.now(),
            )


def test_an_effective_period_must_run_forwards(satnet):
    with pytest.raises(IntegrityError, match="ck_satnet_effective_period"):
        with transaction.atomic():
            record = satnet["satnet"]
            record.effective_until = record.effective_from - timezone.timedelta(days=1)
            record.save()


# ---------------------------------------------------------------------------
# Never re-parented
# ---------------------------------------------------------------------------
def test_a_satnet_cannot_be_moved_to_another_beam(satnet):
    """Re-parenting would change which spectrum resources the allocations underneath compete
    on (ADR-0018) *without touching those allocations* — every reservation would silently
    start being judged against a different pool."""
    other = make_entitlement(code="OTHER")

    with pytest.raises(services.OutOfScopeError, match="cannot be moved"):
        services.update(
            actor=satnet["admin"],
            satnet=satnet["satnet"],
            values={"name": "Renamed", "beam": other.beam},
        )


def test_the_edit_form_does_not_offer_the_beam_or_the_hub(satnet):
    """Refused by the service *and* absent from the form. The service is the guarantee — it is
    reached by more than one form — and the form is what stops somebody trying."""
    from satnets.forms import SatnetEditForm

    fields = set(SatnetEditForm(instance=satnet["satnet"]).fields)

    assert "beam" not in fields
    assert "hub" not in fields


def test_an_ordinary_edit_is_audited_with_the_values_that_were_replaced(satnet):
    from audit.models import AuditEvent

    services.update(
        actor=satnet["admin"],
        satnet=satnet["satnet"],
        values={"customer": "Acme"},
        reason="Customer confirmed",
    )

    event = AuditEvent.objects.filter(action="SATNET_UPDATED").latest("occurred_at")
    assert event.before["customer"] == ""
    assert event.after["customer"] == "Acme"


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------
def test_an_inactive_satnet_takes_no_new_paths(satnet):
    """Deactivating a Satnet is an operational decision that its *live* allocations survive —
    they keep their spectrum until retired individually. What it stops is new work."""
    record = satnet["satnet"]
    assert record.accepts_new_paths is True

    services.set_active(actor=satnet["admin"], satnet=record, active=False, reason="Suspended")
    record.refresh_from_db()

    assert record.is_active is False
    assert record.accepts_new_paths is False


def test_a_satnet_under_a_deactivated_beam_takes_no_new_paths(satnet):
    """§13.9: a Satnet cannot outlive its Beam. Expressed where it is asked rather than as a
    constraint — deactivating the Beam does not rewrite every Satnet under it, and a stored
    copy of the Beam's state would be a second source of truth."""
    from beams import services as beam_services

    record = satnet["satnet"]
    beam_services.set_active(actor=satnet["admin"], beam=record.beam, active=False)
    record.refresh_from_db()

    assert record.is_active is True
    assert record.accepts_new_paths is False


def test_deactivation_is_never_blocked_by_existing_allocations(satnet):
    """Unlike inventory deactivation, which refuses while dependants exist.

    The two are different acts: deactivating a Frequency Window orphans records that depend on
    its engineering values, while deactivating a Satnet is a decision about future work.
    """
    reserve_range(satnet["setup"], 10_000_000, 20_000_000)

    services.set_active(actor=satnet["admin"], satnet=satnet["satnet"], active=False)

    satnet["satnet"].refresh_from_db()
    assert satnet["satnet"].is_active is False


def test_activation_changes_are_audited(satnet):
    from audit.models import AuditEvent

    services.set_active(
        actor=satnet["admin"], satnet=satnet["satnet"], active=False, reason="End of contract"
    )

    event = AuditEvent.objects.get(action="SATNET_DEACTIVATED")
    assert event.before == {"is_active": True}
    assert event.change_reason == "End of contract"


# ---------------------------------------------------------------------------
# Capacity is computed, and it is the Beam's
# ---------------------------------------------------------------------------
def test_capacity_matches_a_hand_computed_figure(satnet):
    """§26.8. The entitlement is 100 MHz; 12 MHz is held including guards; 88 MHz is free."""
    reserve_range(satnet["setup"], 40_000_000, 50_000_000, guard_hz=1_000_000)

    uplink = next(leg for leg in selectors.capacity(satnet["satnet"]) if leg.leg == "HUB_UPLINK")

    assert uplink.summary.total_hz == 100_000_000
    assert uplink.summary.used_hz == 12_000_000
    assert uplink.summary.free_hz == 88_000_000


def test_capacity_counts_allocations_belonging_to_other_satnets(satnet):
    """A Satnet holds no spectrum of its own. Its capacity is the Beam's, and everything on
    the same resources counts against it — which is what stops a per-Satnet figure being a
    subset presented as a whole."""
    reserve_range(satnet["setup"], 40_000_000, 50_000_000)

    uplink = next(leg for leg in selectors.capacity(satnet["satnet"]) if leg.leg == "HUB_UPLINK")

    assert uplink.summary.used_hz == 10_000_000


def test_nothing_about_capacity_is_stored(satnet):
    """§16, ADR-0009. If a column ever appears here it becomes a second source of truth for
    free capacity, and the failure is silent."""
    columns = {field.name for field in Satnet._meta.get_fields() if hasattr(field, "column")}

    assert not {
        name for name in columns if "capacity" in name or "utilisation" in name or "free" in name
    }


# ---------------------------------------------------------------------------
# Selectable
# ---------------------------------------------------------------------------
def test_only_granted_active_satnets_are_selectable(satnet):
    """S11's first wizard step. Offering a Satnet that will be refused two steps later is
    worse than not offering it."""
    operator = make_user("op-sel", roles=[Role.OPERATOR])
    record = satnet["satnet"]

    assert selectors.selectable(operator).count() == 0

    UserBeamScope.objects.create(user=operator, beam=record.beam)
    UserHubScope.objects.create(user=operator, hub=record.hub)

    assert list(selectors.selectable(operator)) == [record]

    services.set_active(actor=satnet["admin"], satnet=record, active=False)

    assert selectors.selectable(operator).count() == 0
