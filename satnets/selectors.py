"""Reading Satnets, and their computed capacity. §6, §16.

Capacity is never stored (ADR-0009). What an operator sees when choosing a Satnet is computed
from the same selector the exclusion constraint will be enforcing against, which is the
property that makes showing a capacity figure worth doing at all — a number from a different
source would be a number that can disagree.
"""

from __future__ import annotations

import dataclasses

from django.db.models import QuerySet

from accounts.types import Actor
from beams.selectors import direction_configs
from calculations.gaps import CapacitySummary
from satnets import scope
from satnets.models import Satnet
from spectrum import selectors as spectrum_selectors


@dataclasses.dataclass(frozen=True)
class LegCapacity:
    """One leg's free capacity, labelled well enough to put on a screen."""

    direction: str
    leg: str
    window_code: str
    summary: CapacitySummary


def visible(actor: Actor) -> QuerySet[Satnet]:
    queryset = Satnet.objects.select_related("beam", "hub", "gateway").order_by(
        "beam__code", "code"
    )
    return scope.visible_to(actor, queryset)


def selectable(actor: Actor) -> QuerySet[Satnet]:
    """Satnets an operator may actually create a Path under, for S11's first wizard step.

    Both grants **and** active, because offering a Satnet that will be refused two steps later
    is worse than not offering it.
    """
    return scope.granted_to(actor, visible(actor)).filter(is_active=True, beam__is_active=True)


def capacity(satnet: Satnet) -> list[LegCapacity]:
    """Free capacity on every leg of the Satnet's Beam. §6.

    A Satnet's capacity **is** its Beam's: the Satnet does not hold spectrum of its own, and
    the allocations under it compete with every other allocation on the same resources —
    including those belonging to other Satnets, and to other Beams entirely (ADR-0018). A
    per-Satnet figure would be a subset presented as a whole.
    """
    result: list[LegCapacity] = []
    for config in direction_configs(satnet.beam):
        if not config.is_enabled or not config.is_configured:
            continue
        for window in (config.uplink_window, config.downlink_window):
            if window is None:
                continue
            result.append(
                LegCapacity(
                    direction=config.direction,
                    leg=window.side,
                    window_code=window.code,
                    summary=spectrum_selectors.capacity(config, leg=window.side),
                )
            )
    return result
