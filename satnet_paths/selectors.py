"""Reading Satnet Paths. §10.3."""

from __future__ import annotations

from django.db.models import QuerySet

from accounts.types import Actor
from satnet_paths import scope
from satnet_paths.models import SatnetPath


def visible(actor: Actor) -> QuerySet[SatnetPath]:
    queryset = SatnetPath.objects.select_related(
        "satnet", "beam", "canonical_window", "translated_window", "equipment_profile"
    ).order_by("satnet__code", "code", "-revision_number")
    return scope.visible_to(actor, queryset)


def current(actor: Actor) -> QuerySet[SatnetPath]:
    """Only the head of each revision chain (§15.4).

    A list showing every revision would show one allocation many times, and the older rows are
    history rather than allocations — `superseded_by` is empty exactly on the current one.
    """
    return visible(actor).filter(superseded_by__isnull=True)
