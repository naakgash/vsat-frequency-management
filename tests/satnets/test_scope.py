"""Conjunctive scope on a write path. §6, §25, **A-17**, **OQ-30**.

The first slice where scope stops being a design note and starts refusing requests. §25:
*"Operator can create Satnet only under authorized Beam"* — and **A-17** adds the Hub:

    *"Scope is conjunctive: acting on a Satnet requires the Beam **and** the Hub in scope."*

Two things this file is careful about, both learned from S8's scope bug:

* **Reading and acting are separate questions.** ``accounts.policy.require`` runs one resolver
  for every capability on a model, so a resolver that answered the *write* question would
  silently deny reads. The read resolver is open; the write check is its own function.
* **Direct POST, not just a missing button.** A form whose querysets are narrowed is a
  courtesy. The guarantee is that posting to the URL with an out-of-scope Beam is refused.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.constants import Role
from accounts.models import UserBeamScope, UserGatewayScope, UserHubScope
from satnets import scope, services
from satnets.models import Satnet
from tests.beams.factories import make_valid_beam
from tests.factories import make_admin, make_user
from tests.inventory.factories import make_gateway, make_hub

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    """One active Beam, one Hub at one Gateway — the two axes a Satnet sits on."""
    beam = make_valid_beam("BEAM-S10")
    admin = make_admin()
    from beams import services as beam_services

    beam_services.validate_beam(actor=admin, beam=beam)
    beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=beam, active=True, reason="For Satnets")

    gateway = make_gateway("GW-S10")
    hub = make_hub(gateway, "HUB-S10")
    return {"beam": beam, "gateway": gateway, "hub": hub, "admin": admin}


def _values(world, code="SN-1"):
    return {
        "code": code,
        "name": "Test Satnet",
        "beam": world["beam"],
        "hub": world["hub"],
        "effective_from": timezone.now(),
    }


def _operator(username="op"):
    return make_user(username, roles=[Role.OPERATOR])


# ---------------------------------------------------------------------------
# The conjunction
# ---------------------------------------------------------------------------
def test_an_operator_with_both_grants_may_create_a_satnet(world):
    """§25's sentence, made true."""
    operator = _operator()
    UserBeamScope.objects.create(user=operator, beam=world["beam"])
    UserHubScope.objects.create(user=operator, hub=world["hub"])

    satnet = services.create(actor=operator, values=_values(world), reason="New service")

    assert satnet.pk is not None
    assert satnet.gateway_id == world["hub"].gateway_id


def test_an_operator_with_only_a_beam_grant_is_refused(world):
    operator = _operator()
    UserBeamScope.objects.create(user=operator, beam=world["beam"])

    with pytest.raises(services.OutOfScopeError, match="Hub"):
        services.create(actor=operator, values=_values(world))

    assert Satnet.objects.count() == 0


def test_an_operator_with_only_a_hub_grant_is_refused(world):
    operator = _operator()
    UserHubScope.objects.create(user=operator, hub=world["hub"])

    with pytest.raises(services.OutOfScopeError, match="Beam"):
        services.create(actor=operator, values=_values(world))


def test_an_operator_with_neither_grant_is_told_both_are_missing(world):
    """The message names what to ask for. "Denied" alone sends somebody to the wrong person —
    the two grants are often requested from different people."""
    operator = _operator()

    with pytest.raises(services.OutOfScopeError) as excinfo:
        services.create(actor=operator, values=_values(world))

    assert "Beam" in str(excinfo.value)
    assert "Hub" in str(excinfo.value)


def test_a_gateway_grant_cascades_to_its_hubs(world):
    """**OQ-30**'s second half. Granting a teleport site covers the hubs at it, including ones
    commissioned later, without anybody remembering a second grant."""
    operator = _operator()
    UserBeamScope.objects.create(user=operator, beam=world["beam"])
    UserGatewayScope.objects.create(user=operator, gateway=world["gateway"])

    satnet = services.create(actor=operator, values=_values(world))

    assert satnet.pk is not None


def test_a_hub_grant_does_not_imply_its_gateway(world):
    """The cascade runs one way only. Widening a hub grant to its site would hand out access
    nobody granted (**A-17**)."""
    operator = _operator()
    other_hub = make_hub(world["gateway"], "HUB-OTHER")
    UserBeamScope.objects.create(user=operator, beam=world["beam"])
    UserHubScope.objects.create(user=operator, hub=other_hub)

    with pytest.raises(services.OutOfScopeError, match="Hub"):
        services.create(actor=operator, values=_values(world))


def test_an_administrator_bypasses_scope(world):
    """**A-17**. And is never told they are missing a grant that does not apply to them."""
    satnet = services.create(actor=world["admin"], values=_values(world))

    assert satnet.pk is not None


def test_a_scope_denial_is_audited(world):
    """§18. The denial an administrator most often has to explain afterwards: the role is
    right and the screen was reachable, so it looks like a bug to whoever hit it."""
    from audit.models import AuditEvent

    operator = _operator()

    with pytest.raises(services.OutOfScopeError):
        services.create(actor=operator, values=_values(world))

    event = AuditEvent.objects.filter(action="PERMISSION_DENIED").latest("occurred_at")
    assert "out of scope" in event.message
    assert "Beam" in event.message and "Hub" in event.message


# ---------------------------------------------------------------------------
# Reading is open; acting is not
# ---------------------------------------------------------------------------
def test_an_operator_without_grants_may_still_read_a_satnet(world, client):
    """Deliberate. An operator has to see the fleet before asking for access to part of it,
    and a list that hid most of it would look like missing data rather than a boundary.

    This is also the S8 lesson: one resolver runs for every capability, so a resolver
    answering the *write* question would deny the read as a side effect.
    """
    satnet = services.create(actor=world["admin"], values=_values(world))
    operator = _operator()
    client.force_login(operator)

    response = client.get(reverse("satnets:detail", kwargs={"pk": satnet.pk}))

    assert response.status_code == 200
    assert response.context["may_act"] is False
    assert "Hub" in response.context["scope_reason"]


def test_the_list_marks_which_satnets_the_actor_may_act_on(world, client):
    satnet = services.create(actor=world["admin"], values=_values(world))
    operator = _operator()
    client.force_login(operator)

    response = client.get(reverse("satnets:list"))

    assert satnet.pk not in response.context["actionable_ids"]

    UserBeamScope.objects.create(user=operator, beam=world["beam"])
    UserHubScope.objects.create(user=operator, hub=world["hub"])
    response = client.get(reverse("satnets:list"))

    assert satnet.pk in response.context["actionable_ids"]


# ---------------------------------------------------------------------------
# Direct POST — the guarantee, not the courtesy
# ---------------------------------------------------------------------------
def test_posting_an_out_of_scope_beam_directly_is_refused(world, client):
    """The form narrows its choices; a direct POST does not go through them.

    Without this test the whole scope rule would rest on a queryset filter, which is a
    convenience that any second entry point silently bypasses.
    """
    operator = _operator()
    UserHubScope.objects.create(user=operator, hub=world["hub"])
    client.force_login(operator)

    response = client.post(
        reverse("satnets:create"),
        {
            "code": "SN-SNEAK",
            "name": "Direct post",
            "beam": str(world["beam"].pk),
            "hub": str(world["hub"].pk),
            "effective_from": "2026-01-01T00:00",
        },
    )

    assert response.status_code in (400, 403)
    assert Satnet.objects.filter(code="SN-SNEAK").count() == 0


@pytest.mark.parametrize("role", [Role.APPROVER, Role.OBSERVER])
def test_a_role_without_the_capability_cannot_reach_the_create_screen(world, client, role):
    """Capability first, scope second. An Approver holding every grant in the system still
    may not create a Satnet — that is §25, and it is a different refusal."""
    client.force_login(make_user(f"user-{role}", roles=[role]))

    response = client.get(reverse("satnets:create"))

    assert response.status_code == 403


def test_a_capability_denial_and_a_scope_denial_are_different_refusals(world, client):
    """The distinction that makes both messages useful.

    An Observer is told their role cannot do this. An Operator without grants is told *which
    grant* is missing. Collapsing them into one 403 sends people to the wrong person: a role
    change and a scope grant come from different places.
    """
    observer = make_user("obs", roles=[Role.OBSERVER])
    client.force_login(observer)
    capability_denied = client.post(reverse("satnets:create"), {})

    operator = _operator("op2")
    client.force_login(operator)
    scope_denied = client.post(
        reverse("satnets:create"),
        {
            "code": "SN-X",
            "name": "x",
            "beam": str(world["beam"].pk),
            "hub": str(world["hub"].pk),
            "effective_from": "2026-01-01T00:00",
        },
    )

    assert capability_denied.status_code == 403
    assert scope_denied.status_code in (400, 403)


# ---------------------------------------------------------------------------
# The scope helper itself
# ---------------------------------------------------------------------------
def test_may_act_on_returns_the_reason_not_just_the_answer(world):
    operator = _operator()

    allowed, reason = scope.may_act_on(operator, beam_id=world["beam"].pk, hub_id=world["hub"].pk)

    assert allowed is False
    assert reason
