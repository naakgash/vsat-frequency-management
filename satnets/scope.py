"""Object-level scope for Satnets. §6, §25, **A-17**, **OQ-30**.

This is where the conjunctive rule becomes real. Every other scoped object in the product has
one axis; a Satnet has two, and **A-17** says both must be granted:

    *"Scope is conjunctive: acting on a Satnet requires the Beam **and** the Hub in scope."*

The word that matters is *acting*. Reading is open — an operator has to be able to see the
fleet before asking for access to part of it, and a list that silently hid most of it would
look like missing data rather than like a boundary. So this resolver answers the read
question, and ``satnets.services`` asks the write question separately and refuses with a
message naming **which** grant is missing.

Splitting them is deliberate. ``accounts.policy.require`` runs one resolver for *every*
capability on a model, so a resolver that answered the write question would deny reads too —
the mistake ``beams/scope.py`` made in S8 and that ``test_an_operator_may_validate_a_beam``
caught.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q, QuerySet

from accounts.models import UserBeamScope
from accounts.types import Actor
from inventory.scope import effective_hub_ids
from satnets.models import Satnet


def granted_beam_ids(actor: Any) -> set[uuid.UUID]:
    """Beams explicitly granted to this actor. No cascade — nothing implies a Beam grant."""
    return set(UserBeamScope.objects.filter(user=actor).values_list("beam_id", flat=True))


def satnet_in_scope(actor: Actor, satnet: Satnet) -> bool:
    """Is this Satnet within the actor's object scope, for reading?

    Open to any authenticated user, matching Beams. The write side is
    :func:`satnets.services.check_scope`, which requires both grants and says which is
    missing.
    """
    return bool(getattr(actor, "is_authenticated", False))


def may_act_on(actor: Any, *, beam_id: uuid.UUID, hub_id: uuid.UUID) -> tuple[bool, str]:
    """May this actor create or change a Satnet on this Beam and Hub? **A-17**.

    Returns the answer *and the reason*, because "denied" alone is not actionable: an operator
    with a Hub grant and no Beam grant needs to know which one to ask for, and the two are
    requested from different people.

    Administrators bypass scope entirely (**A-17**), which is checked first — an administrator
    should never be told they are missing a grant that does not apply to them.
    """
    if getattr(actor, "is_superuser", False) or actor.has_perm("accounts.manage_scopes"):
        return True, ""

    has_beam = beam_id in granted_beam_ids(actor)
    has_hub = hub_id in effective_hub_ids(actor)

    if has_beam and has_hub:
        return True, ""
    if not has_beam and not has_hub:
        return False, "You have no scope grant for this Beam or this Hub."
    if not has_beam:
        return False, "You have no scope grant for this Beam."
    return False, "You have no scope grant for this Hub."


def visible_to(actor: Actor, queryset: QuerySet[Satnet]) -> QuerySet[Satnet]:
    """Satnets this actor may see.

    Deliberately **not** narrowed to granted objects, for the reason in the module docstring.
    The signature takes a queryset so that narrowing it later is a change to one function
    rather than to every call site.
    """
    if not getattr(actor, "is_authenticated", False):
        return queryset.none()
    return queryset


def granted_to(actor: Any, queryset: QuerySet[Satnet]) -> QuerySet[Satnet]:
    """Satnets this actor may *act* on — both grants held. Used by the S11 wizard."""
    if getattr(actor, "is_superuser", False) or actor.has_perm("accounts.manage_scopes"):
        return queryset
    return queryset.filter(
        Q(beam_id__in=granted_beam_ids(actor)) & Q(hub_id__in=effective_hub_ids(actor))
    )
