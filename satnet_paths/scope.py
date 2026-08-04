"""Object-level scope for Satnet Paths. §6, §25, **A-17**.

A Path's scope is its Satnet's: the Satnet is what sits at the intersection of the Beam and Hub
axes, and a Path adds no third axis of its own.

Same split as `satnets.scope`, and for the same reason. ``accounts.policy.require`` runs one
resolver for **every** capability on a model, so a resolver that answered the *write* question
would silently deny reads. This answers the read question; the write question is asked by
``satnet_paths.services.create``, which delegates to ``satnets.scope.may_act_on`` so that the
two records can never disagree about who may act on them.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from accounts.types import Actor
from satnet_paths.models import SatnetPath
from satnets import scope as satnet_scope


def satnet_path_in_scope(actor: Actor, path: SatnetPath) -> bool:
    """Is this Path within the actor's object scope, for reading?

    Open to any authenticated user, matching Satnets and Beams. An Approver deciding on an
    allocation and an Observer reporting on one both need to read Paths they hold no grant for.
    """
    return bool(getattr(actor, "is_authenticated", False))


def may_act_on(actor: Any, path: SatnetPath) -> tuple[bool, str]:
    """May this actor change this Path? Delegates to its Satnet's grants (**A-17**)."""
    return satnet_scope.may_act_on(actor, beam_id=path.beam_id, hub_id=path.satnet.hub_id)


def visible_to(actor: Actor, queryset: QuerySet[SatnetPath]) -> QuerySet[SatnetPath]:
    if not getattr(actor, "is_authenticated", False):
        return queryset.none()
    return queryset
