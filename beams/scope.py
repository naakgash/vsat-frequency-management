"""Object-level scope for Beams. Specification section 6, assumption **A-17**.

Registered from ``BeamsConfig.ready()`` into ``accounts.scope``, which is what lets
``accounts`` enforce object scope without importing a domain module.

**Scope is not capability, and conflating the two is a mistake worth naming.**
``accounts.policy.require`` checks the capability first and *then* asks a resolver whether
this particular object is in scope. A resolver that answers "only if you hold
``manage_beams``" therefore does not restrict administrators to their own Beams — it
silently denies an Operator the *read* their capability grants, because the same resolver
runs for every capability on this model. The first version of this module did exactly that,
and ``test_an_operator_may_validate_a_beam`` caught it.
"""

from __future__ import annotations

from django.db.models import QuerySet

from accounts.types import Actor
from beams.models import Beam


def beam_in_scope(actor: Actor, beam: Beam) -> bool:
    """Is this Beam within the actor's object scope?

    Every Beam is, for now. Beam-level scope *grants* land with the Satnet Path wizard in
    S11, where an Operator first needs one; until those rows exist there is nothing to
    narrow by, and inventing a narrowing rule before the grants exist would deny access on a
    basis no administrator could see or change.

    That is not a hole. **A-17**'s deny-by-default is carried by the capability matrix here:
    ``manage_beams`` belongs to administrators alone (§25), so an Operator reaching this
    function for a write has already been refused on the capability. What this decides is
    which Beams a *reader* may reach, and today the answer is all of them.
    """
    return bool(getattr(actor, "is_authenticated", False))


def visible_to(actor: Actor, queryset: QuerySet[Beam]) -> QuerySet[Beam]:
    """Beams this actor may see.

    An Operator has to be able to choose a Beam when creating a Satnet Path, so reading is
    open to any authenticated user. Narrowing this to granted Beams is an S11 change, when
    the grants exist to narrow it by.
    """
    if not getattr(actor, "is_authenticated", False):
        return queryset.none()
    return queryset
