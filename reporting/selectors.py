"""Reading the table and the dashboard. §10.3, §16, §26.11.

**One query for the table, whatever the columns.** A Satnet Path row can reach its Satnet, its
Beam, its Hub, its Gateway and its Decimator, and a table of two hundred rows that follows those
one at a time is a thousand queries. The joins are stated here rather than left to whichever
column happened to be selected — which is why ``table`` takes the chosen columns and does not
consult them: selecting the joins per column would make the query plan depend on a checkbox.

**The dashboard counts what the selectors already know.** §16 forbids a second source of truth
for free capacity, and the same reasoning applies to every figure here: no counter is stored, no
total is cached. A dashboard card is a query, and if that ever becomes too slow the answer is an
index, not a denormalised column that can be wrong.
"""

from __future__ import annotations

import dataclasses

from django.db.models import Count, QuerySet

from accounts.types import Actor
from beams.models import Beam
from reporting import columns as column_registry
from reporting import filters as filter_registry
from reporting.models import SavedView
from satnet_paths import selectors as path_selectors
from satnet_paths.constants import PathStatus
from satnet_paths.models import SatnetPath

#: Everything a row can reach, joined once. Listed here rather than derived from the chosen
#: columns so that the number of queries a page runs does not depend on which boxes are ticked.
ROW_RELATIONS = (
    "satnet",
    "satnet__hub",
    "beam",
    "gateway",
    "decimator_assignment",
    "decimator_assignment__decimator",
)


def table(
    actor: Actor,
    *,
    filters: dict[str, str] | None = None,
    sort: str = "",
) -> QuerySet[SatnetPath]:
    """The current revision of every allocation this actor may see, filtered and sorted.

    Scope is applied by ``satnet_paths.selectors.current`` before anything here narrows it
    further, so a filter can only ever *reduce* what somebody sees (`docs/design/03` §4).
    """
    queryset = path_selectors.current(actor).select_related(*ROW_RELATIONS)
    queryset = filter_registry.apply(queryset, filters or {})
    return queryset.order_by(*column_registry.ordering_for(sort))


def views_for(actor: Actor, page: str = "satnet_paths") -> QuerySet[SavedView]:
    """A person's own views, plus the ones anybody chose to share. §10.3.

    Sharing a view shares the *question*, never the answer: the table it produces is
    scope-filtered on every read, so a shared view shows each reader only their own spectrum.
    """
    from django.db.models import Q

    if not getattr(actor, "is_authenticated", False):
        return SavedView.objects.none()
    return (
        SavedView.objects.filter(page=page)
        .filter(Q(owner=actor) | Q(is_shared=True))
        .select_related("owner")
    )


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class StatusCount:
    status: str
    label: str
    count: int

    @property
    def reserves_spectrum(self) -> bool:
        return self.status in {
            PathStatus.PLANNED,
            PathStatus.PENDING_APPROVAL,
            PathStatus.ON_AIR,
        }


@dataclasses.dataclass(frozen=True)
class BeamUtilisation:
    """One Beam's occupancy on its canonical leg, for the dashboard's capacity card. §16."""

    beam: Beam
    direction: str
    leg: str
    total_hz: int
    free_hz: int
    largest_gap_hz: int

    @property
    def used_hz(self) -> int:
        return self.total_hz - self.free_hz

    @property
    def percent_used(self) -> int:
        """Whole percent, for a progress bar and nothing else.

        Rounded to an integer deliberately: it is a bar on a screen, and a figure with decimal
        places would invite somebody to quote it. Every number anything is *derived* from stays
        in integer Hz (**A-08**).
        """
        return 0 if self.total_hz == 0 else round(100 * self.used_hz / self.total_hz)


@dataclasses.dataclass(frozen=True)
class Dashboard:
    """What the front page says. Every field is a query, none of it is stored."""

    statuses: tuple[StatusCount, ...]
    total_paths: int
    awaiting_approval: int
    active_beams: int
    satnets: int
    utilisation: tuple[BeamUtilisation, ...]

    @property
    def reserving_paths(self) -> int:
        return sum(entry.count for entry in self.statuses if entry.reserves_spectrum)


def dashboard(actor: Actor, *, utilisation_limit: int = 8) -> Dashboard:
    """The front page, within the reader's scope. §26.11.

    ``utilisation_limit`` is a bound on work, not a page size: free capacity is computed per
    Beam direction through the gap engine, which is a query each. A dashboard that walked every
    Beam on a large install would be the slowest page in the product, so it shows the first few
    and the Beam list holds the rest.
    """
    from satnets import selectors as satnet_selectors

    visible = path_selectors.current(actor)
    counts = {
        row["status"]: row["total"] for row in visible.values("status").annotate(total=Count("id"))
    }
    statuses = tuple(
        StatusCount(status=value, label=label, count=counts.get(value, 0))
        for value, label in PathStatus.choices
    )

    beams = list(_visible_beams(actor)[:utilisation_limit])
    return Dashboard(
        statuses=statuses,
        total_paths=sum(counts.values()),
        awaiting_approval=counts.get(PathStatus.PENDING_APPROVAL, 0),
        active_beams=_visible_beams(actor).count(),
        satnets=satnet_selectors.visible(actor).count(),
        utilisation=tuple(_utilisation_of(beams)),
    )


def _visible_beams(actor: Actor) -> QuerySet[Beam]:
    from beams import selectors as beam_selectors

    return beam_selectors.visible(actor).filter(is_active=True)


def _utilisation_of(beams: list[Beam]) -> list[BeamUtilisation]:
    """Free capacity per enabled direction, straight from the gap engine.

    Not a second calculation: this calls the same ``spectrum.selectors.capacity`` the Beam's own
    page calls, so a dashboard that disagreed with a Beam screen would be a bug in one place
    rather than a difference of opinion between two.
    """
    from spectrum import selectors as spectrum_selectors

    entries: list[BeamUtilisation] = []
    for beam in beams:
        for config in beam.direction_configs.all():
            path = config.payload_path
            if not config.is_enabled or path is None:
                continue
            # A direction whose canonical leg was never set falls back to its payload path's
            # uplink side (**A-07**), which is the same default the wizard uses.
            leg = config.canonical_leg or path.uplink_window_side
            summary = spectrum_selectors.capacity(config, leg=leg)
            if summary.total_hz == 0:
                continue
            entries.append(
                BeamUtilisation(
                    beam=beam,
                    direction=config.direction,
                    leg=leg,
                    total_hz=summary.total_hz,
                    free_hz=summary.free_hz,
                    largest_gap_hz=summary.largest_gap_hz,
                )
            )
    return entries
