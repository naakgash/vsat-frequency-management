"""The wizard over HTTP. sections 9.2 to 9.5, §25, §26.16.

The service tests prove the rules. These prove the screens route to them — including the two
that are easy to get wrong at the view layer: a refused allocation must not come back as a
form error, and no role may bind a derived field.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.constants import Role
from accounts.models import UserBeamScope, UserHubScope
from satnet_paths.constants import InputMode, PathStatus
from satnet_paths.models import SatnetPath
from satnets import services as satnet_services
from spectrum.models import SpectrumReservation
from tests.factories import make_admin, make_user
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


@pytest.fixture
def world():
    setup = make_entitlement(code="WZ", start_hz=0, end_hz=100 * MHZ)
    admin = make_admin()
    from beams import services as beam_services

    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    hub = make_hub(make_gateway("GW-WZ"), "HUB-WZ")
    satnet = satnet_services.create(
        actor=admin,
        values={
            "code": "SN-WZ",
            "name": "Wizard",
            "beam": setup.beam,
            "hub": hub,
            "effective_from": timezone.now() - timezone.timedelta(days=1),
        },
    )
    return {"setup": setup, "satnet": satnet, "admin": admin, "hub": hub}


def _post(centre=50 * MHZ, **extra):
    data = {
        "code": "WZ-1",
        "direction": "FWD",
        "input_mode": InputMode.OCCUPIED_BW,
        "input_value": 10 * MHZ,
        "rolloff": "0.2",
        "canonical_center_hz": centre,
        "valid_from": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "gw_id": "",
        "decimator": "",
    }
    data.update(extra)
    return data


def _url(world, name="create"):
    return reverse(f"satnet_paths:{name}", kwargs={"satnet_pk": world["satnet"].pk})


def _grant(world, user):
    UserBeamScope.objects.create(user=user, beam=world["satnet"].beam)
    UserHubScope.objects.create(user=user, hub=world["hub"])


# ---------------------------------------------------------------------------
# Preview and save
# ---------------------------------------------------------------------------
def test_preview_computes_without_saving(client, world):
    """§9.3/§9.4. The preview is a courtesy and writes nothing — which is what makes it safe to
    recompute on every keystroke."""
    client.force_login(world["admin"])

    response = client.post(_url(world), _post(action="preview"))

    assert response.status_code == 200
    assert response.context["proposal"].ok
    assert SatnetPath.objects.count() == 0
    assert SpectrumReservation.objects.count() == 0


def test_saving_creates_the_path_and_its_reservations(client, world):
    client.force_login(world["admin"])

    response = client.post(_url(world), _post(status=PathStatus.PLANNED), follow=True)

    assert response.status_code == 200
    path = SatnetPath.objects.get()
    assert SpectrumReservation.objects.filter(satnet_path_id=path.pk).count() == 2


def test_a_blocked_allocation_returns_409_with_the_findings(client, world):
    """409, not 400. The submission is well-formed and was refused by a rule about the world,
    not by a field the operator can retype — and a form error would tell them to fix the
    frequency box when the actual problem is somebody else's transmission."""
    reserve_range(world["setup"], 48 * MHZ, 52 * MHZ)
    client.force_login(world["admin"])

    response = client.post(_url(world), _post(status=PathStatus.PLANNED))

    assert response.status_code == 409
    codes = {finding.code for finding in response.context["findings"]}
    assert "SPECTRUM_CONFLICT" in codes
    assert SatnetPath.objects.count() == 0


def test_the_blocking_screen_shows_what_section_9_5_asks_for(client, world):
    reserve_range(world["setup"], 48 * MHZ, 52 * MHZ)
    client.force_login(world["admin"])

    response = client.post(_url(world), _post(status=PathStatus.PLANNED))

    body = response.content.decode()
    assert "Overlap" in body
    assert "Free spectrum on this leg" in body
    assert world["satnet"].beam.code in body


def test_auto_place_offers_a_centre_and_saves_nothing(client, world):
    client.force_login(world["admin"])

    response = client.post(_url(world, "auto-place"), _post())

    assert response.status_code == 200
    assert response.context["auto_placed"] is True
    assert SatnetPath.objects.count() == 0


def test_auto_place_says_so_when_nothing_fits(client, world):
    reserve_range(world["setup"], 0, 100 * MHZ)
    client.force_login(world["admin"])

    response = client.post(_url(world, "auto-place"), _post())

    assert response.context["auto_place_failed"] is True
    assert "No gap on this leg is wide enough" in response.content.decode()


# ---------------------------------------------------------------------------
# §26.16 — derived fields are unbindable, for every role
# ---------------------------------------------------------------------------
def test_the_form_binds_no_derived_field():
    """The field list *is* the guarantee. A test on the list itself, so that adding a derived
    field to it fails here rather than silently handing an operator control of the engine's
    output."""
    from satnet_paths.forms import SatnetPathForm

    bound = set(SatnetPathForm().fields)
    derived = {
        "beam",
        "occupied_bw_hz",
        "allocated_bw_hz",
        "symbol_rate_sps",
        "canonical_occupied_start_hz",
        "canonical_allocated_start_hz",
        "translated_center_hz",
        "translated_allocated_end_hz",
        "canonical_assignment",
        "translated_assignment",
        "lo_hz",
        "if_start_hz",
        "revision_number",
    }

    assert not (bound & derived)


def test_posting_derived_fields_over_http_changes_nothing(client, world):
    """§26.16 end to end, not just at the form."""
    client.force_login(world["admin"])

    client.post(
        _url(world),
        _post(
            status=PathStatus.PLANNED,
            occupied_bw_hz=1,
            allocated_bw_hz=1,
            symbol_rate_sps=1,
            canonical_allocated_start_hz=0,
            canonical_allocated_end_hz=1,
        ),
    )

    path = SatnetPath.objects.get()
    assert path.occupied_bw_hz > 1
    assert path.canonical_allocated_end_hz > 1


# ---------------------------------------------------------------------------
# §25 — who may do this
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", [Role.APPROVER, Role.OBSERVER])
def test_a_role_without_the_capability_cannot_reach_the_wizard(client, world, role):
    client.force_login(make_user(f"u-{role}", roles=[role]))

    assert client.get(_url(world)).status_code == 403
    assert client.post(_url(world), _post()).status_code == 403


def test_an_operator_with_both_grants_may_create_a_path(client, world):
    operator = make_user("op-wz", roles=[Role.OPERATOR])
    _grant(world, operator)
    client.force_login(operator)

    client.post(_url(world), _post(status=PathStatus.PLANNED))

    assert SatnetPath.objects.count() == 1


def test_an_operator_without_grants_is_refused_on_post(client, world):
    """**A-17** reaches the wizard: the capability is held, the grants are not."""
    operator = make_user("op-nogrant", roles=[Role.OPERATOR])
    client.force_login(operator)

    response = client.post(_url(world), _post(status=PathStatus.PLANNED))

    assert response.status_code == 409
    assert response.context["findings"][0].code == "OUT_OF_SCOPE"
    assert SatnetPath.objects.count() == 0


def test_every_role_may_read_the_list(client, world):
    for role in (Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER):
        client.force_login(make_user(f"reader-{role}", roles=[role]))
        assert client.get(reverse("satnet_paths:list")).status_code == 200


def test_the_list_shows_only_the_current_revision(client, world):
    """§15.4. Older revisions are history; showing them all would list one allocation many
    times, and `superseded_by` is empty exactly on the current one."""
    client.force_login(world["admin"])
    client.post(_url(world), _post(status=PathStatus.PLANNED))
    path = SatnetPath.objects.get()

    response = client.get(reverse("satnet_paths:list"))

    assert list(response.context["paths"]) == [path]


def test_a_missing_value_is_a_form_error_rather_than_a_crash(client, world):
    """§9.2: one mode, one value. A mode without a value produces a zero bandwidth further
    downstream, which is a much worse error than a field message."""
    client.force_login(world["admin"])

    response = client.post(_url(world), _post(input_value=""))

    assert response.status_code == 400
    assert SatnetPath.objects.count() == 0
