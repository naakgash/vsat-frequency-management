"""Satnet write paths. §13.9, §25, ADR-0013.

Authorise, then transact — and here "authorise" is two questions, not one: the capability
(may this role create Satnets at all) and the **conjunctive scope** (may this actor act on
this Beam *and* this Hub).
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from accounts import policy
from accounts.types import Actor
from audit import services as audit_services
from satnets import scope
from satnets.constants import (
    MANAGE_SATNETS,
    SATNET_CREATED,
    SATNET_DEACTIVATED,
    SATNET_REACTIVATED,
    SATNET_UPDATED,
)
from satnets.models import Satnet


class OutOfScopeError(Exception):
    """The actor holds the capability but not the grants this object needs.

    Distinct from a capability denial on purpose. "Operators cannot create Satnets" and "you
    cannot create a Satnet *here*" are different problems with different remedies — the first
    is a role change, the second a scope grant — and collapsing them into one 403 sends people
    to the wrong person.
    """


def check_scope(actor: Actor, *, beam_id: Any, hub_id: Any) -> None:
    """Refuse unless both grants are held, naming the missing one. **A-17**, §25.

    Audited on refusal. §18 requires denials to be recorded, and a scope denial is the one an
    administrator most often has to explain afterwards: it looks like a bug to the person who
    hit it, because their role is right and the screen was reachable.
    """
    allowed, reason = scope.may_act_on(actor, beam_id=beam_id, hub_id=hub_id)
    if allowed:
        return
    policy.record_denial(actor, MANAGE_SATNETS, None, detail=f"out of scope: {reason}")
    raise OutOfScopeError(reason)


def create(*, actor: Actor, values: dict[str, Any], reason: str = "") -> Satnet:
    """Create a Satnet under a Beam and Hub the actor is authorised for.

    The capability is checked first and the scope second, which is the order that produces the
    right message: a role that may not create Satnets at all should be told that, rather than
    being told which grant it is missing.
    """
    policy.require(actor, MANAGE_SATNETS, reason=reason)
    check_scope(actor, beam_id=values["beam"].pk, hub_id=values["hub"].pk)
    return _create(actor=actor, values=values, reason=reason)


@transaction.atomic
def _create(*, actor: Actor, values: dict[str, Any], reason: str) -> Satnet:
    # Derived, never bound from the form: the Gateway is a fact about the Hub, and letting it
    # be submitted would allow a row claiming a Gateway the Hub is not at. The composite
    # foreign key would refuse it, but by then the error is a constraint name rather than a
    # field.
    satnet = Satnet(**values, gateway=values["hub"].gateway)
    satnet.full_clean(exclude=["created_by", "updated_by"])
    satnet.save()

    audit_services.record(
        action=SATNET_CREATED,
        actor=actor,
        obj=satnet,
        after=audit_services.snapshot(satnet),
        change_reason=reason,
        message=f"Created Satnet {satnet}",
    )
    return satnet


def update(*, actor: Actor, satnet: Satnet, values: dict[str, Any], reason: str = "") -> Satnet:
    """Edit a Satnet. Its Beam and Hub are not among the things that can be edited.

    Re-parenting would change which spectrum resources the allocations underneath compete on
    (ADR-0018) without touching those allocations — every reservation would silently start
    being judged against a different pool. The form does not offer the fields; this refuses
    them anyway, because a service is reached by more than one form.
    """
    policy.require(actor, MANAGE_SATNETS, satnet, reason=reason)
    check_scope(actor, beam_id=satnet.beam_id, hub_id=satnet.hub_id)

    for field in ("beam", "hub", "beam_id", "hub_id", "gateway", "gateway_id"):
        if field in values:
            raise OutOfScopeError(
                "A Satnet cannot be moved to another Beam or Hub. Create a new Satnet: the "
                "allocations underneath this one are judged against its current Beam's "
                "spectrum resources."
            )
    return _update(actor=actor, satnet=satnet, values=values, reason=reason)


@transaction.atomic
def _update(*, actor: Actor, satnet: Satnet, values: dict[str, Any], reason: str) -> Satnet:
    before = audit_services.snapshot(satnet)
    for field, value in values.items():
        setattr(satnet, field, value)
    satnet.full_clean(exclude=["created_by", "updated_by"])
    satnet.save()

    audit_services.record(
        action=SATNET_UPDATED,
        actor=actor,
        obj=satnet,
        before=before,
        after=audit_services.snapshot(satnet),
        change_reason=reason,
        message=f"Updated Satnet {satnet}",
    )
    return satnet


def set_active(*, actor: Actor, satnet: Satnet, active: bool, reason: str = "") -> Satnet:
    """Activate or deactivate a Satnet.

    Deactivation is **not** blocked by existing Satnet Paths, unlike inventory deactivation.
    The two are different acts: deactivating a Frequency Window would orphan things that
    reference its engineering values, while deactivating a Satnet is an operational decision
    that its live allocations survive — they keep their spectrum until they are retired
    individually. What deactivation does stop is *new* paths, which
    :attr:`Satnet.accepts_new_paths` answers.
    """
    policy.require(actor, MANAGE_SATNETS, satnet, reason=reason)
    check_scope(actor, beam_id=satnet.beam_id, hub_id=satnet.hub_id)
    return _set_active(actor=actor, satnet=satnet, active=active, reason=reason)


@transaction.atomic
def _set_active(*, actor: Actor, satnet: Satnet, active: bool, reason: str) -> Satnet:
    before = satnet.is_active
    satnet.is_active = active
    satnet.save(update_fields=["is_active", "updated_at"])

    audit_services.record(
        action=SATNET_REACTIVATED if active else SATNET_DEACTIVATED,
        actor=actor,
        obj=satnet,
        before={"is_active": before},
        after={"is_active": active},
        change_reason=reason,
        message=f"{'Reactivated' if active else 'Deactivated'} Satnet {satnet.code}",
    )
    return satnet
