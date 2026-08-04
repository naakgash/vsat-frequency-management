"""Beam reads.

Cross-module reads go through a selector rather than reaching into another module's ORM
(docs/design/01 §1.2). S10's Satnet and S11's Satnet Path both need "which Beams may this
user pick from", and they get it here rather than each writing their own query.
"""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from accounts.types import Actor
from beams import scope
from beams.constants import ConfigurationState
from beams.models import Beam, BeamDirectionConfig, BeamValidationResult


def visible(actor: Actor) -> QuerySet[Beam]:
    """Every Beam this actor may see, in spectrum-planning order."""
    queryset = Beam.objects.select_related("satellite", "band").order_by("code")
    return scope.visible_to(actor, queryset)


def for_listing(actor: Actor) -> QuerySet[Beam]:
    """Visible Beams with their direction counts, for the list screen.

    Annotated rather than prefetched: the list shows how many directions are enabled, not
    which ones, and counting in the database avoids loading two child rows per Beam to
    discard them.

    Explicit ``order_by``: an aggregate annotation adds a ``GROUP BY`` that drops
    ``Meta.ordering``, and paginating without an ``ORDER BY`` can repeat or skip rows — the
    same trap S4 hit.
    """
    return (
        visible(actor)
        .annotate(
            # Deliberately not "enabled_directions": Beam already has a property of
            # that name returning a list of configs, and an annotation silently shadows a
            # property rather than raising. The template would then render a count where a
            # reader of the model would expect rows.
            enabled_direction_count=Count(
                "direction_configs", filter=Q(direction_configs__is_enabled=True)
            )
        )
        .order_by("code")
    )


def selectable(actor: Actor) -> QuerySet[Beam]:
    """Beams a Satnet or Satnet Path may be attached to.

    Active **and** valid. A Beam whose configuration has gone invalid since activation
    cannot be reached this way, which is the read-side half of §26.6 — the write side is
    the activation refusal in ``beams.services``.
    """
    return visible(actor).filter(is_active=True, configuration_state=ConfigurationState.VALID)


def direction_configs(beam: Beam) -> list[BeamDirectionConfig]:
    """Both directions, always both, in a fixed order.

    Both rows exist from creation, so a missing one is a data problem rather than a normal
    state — and returning them in a fixed order means FWD is always the first tab.
    """
    return list(
        beam.direction_configs.select_related(
            "payload_path",
            "payload_path__satellite",
            "uplink_window",
            "downlink_window",
        )
        .prefetch_related("equipment_profiles__equipment_profile")
        .order_by("direction")
    )


def latest_validation(beam: Beam) -> BeamValidationResult | None:
    """The most recent validation run, or ``None`` if it has never been validated."""
    return beam.validation_results.select_related("ran_by").first()
