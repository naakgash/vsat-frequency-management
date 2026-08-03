"""Object-level scope for inventory, and the resolvers registered with ``accounts``.

Specification section 6 and design assumption A-17. This module answers **OQ-30**, which
had two parts:

1. *Is scope conjunctive?* Yes for objects that have both a Beam and a Hub — a Satnet in
   S10. Gateway and Hub each have a single axis, so the question does not arise here; the
   conjunction is implemented where it applies.
2. *Does a Gateway grant cascade to its Hubs?* **Yes.** Granting a teleport site should
   not require enumerating every hub at it, and a hub commissioned later should be covered
   without anyone remembering to add a second grant. The reverse does not hold: a Hub
   grant is narrower than a site grant, and widening it would hand out access nobody
   granted.

``accounts`` cannot import this module — it sits below inventory — so the resolvers are
pushed into the registry from :meth:`InventoryConfig.ready`.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import Q

from accounts.managers import ScopedQuerySet
from accounts.models import UserGatewayScope, UserHubScope


def granted_gateway_ids(user: Any) -> set[uuid.UUID]:
    """Gateways explicitly granted to this user."""
    return set(UserGatewayScope.objects.filter(user=user).values_list("gateway_id", flat=True))


def effective_hub_ids(user: Any) -> set[uuid.UUID]:
    """Hubs this user may act on: directly granted, plus every hub at a granted Gateway."""
    from inventory.models import Hub

    direct = set(UserHubScope.objects.filter(user=user).values_list("hub_id", flat=True))
    cascaded = set(
        Hub.objects.filter(gateway_id__in=granted_gateway_ids(user)).values_list("id", flat=True)
    )
    return direct | cascaded


# ---------------------------------------------------------------------------
# Resolvers (accounts.scope signature: (user, obj) -> bool)
# ---------------------------------------------------------------------------
def gateway_in_scope(user: Any, gateway: Any) -> bool:
    return gateway.pk in granted_gateway_ids(user)


def hub_in_scope(user: Any, hub: Any) -> bool:
    """A hub is in scope through its own grant or through its Gateway's."""
    if UserHubScope.objects.filter(user=user, hub=hub).exists():
        return True
    return hub.gateway_id in granted_gateway_ids(user)


# ---------------------------------------------------------------------------
# Scoped querysets (docs/design/03 section 3.3)
# ---------------------------------------------------------------------------
class GatewayQuerySet(ScopedQuerySet):
    def scope_filter(self, user: Any) -> GatewayQuerySet:
        return self.filter(user_scopes__user=user).distinct()


class HubQuerySet(ScopedQuerySet):
    def scope_filter(self, user: Any) -> HubQuerySet:
        # One query rather than two: the direct grant and the cascade are a single OR.
        return self.filter(
            Q(user_scopes__user=user) | Q(gateway__user_scopes__user=user)
        ).distinct()
