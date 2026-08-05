"""Turning a query string into a queryset. §10.3.

Two rules hold this module together.

**Every filter is declared, and nothing else reaches the ORM.** The parameters come from a URL
and a URL is user input; a filter layer that passed unknown keys through to ``filter(**params)``
would let a visitor query columns no screen offers, including ones scope was meant to hide.
:data:`FILTERS` is the whole of what may be asked.

**A filter that cannot be parsed is dropped, not fatal.** A saved view outlives the status it
names and a hand-edited URL is routine. Reporting a table with one filter ignored beats a 500
on a page somebody reached from a bookmark.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import Any

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_datetime

from satnet_paths.constants import PathStatus
from satnet_paths.models import SatnetPath


@dataclasses.dataclass(frozen=True)
class Filter:
    """One thing a table may be narrowed by."""

    key: str
    label: str
    #: Builds the ``Q`` for a non-empty value, or returns None to ignore it.
    build: Callable[[str], Q | None]
    #: Choices for a select control, as ``(value, label)``. Empty means a free-text box.
    choices: tuple[tuple[str, str], ...] = ()
    help_text: str = ""


def _status(value: str) -> Q | None:
    return Q(status=value) if value in PathStatus.values else None


def _direction(value: str) -> Q | None:
    return Q(direction=value) if value in {"FWD", "RTN"} else None


def _text(value: str) -> Q | None:
    """Code search across the allocation and the records above it.

    Case-insensitive `contains` rather than a trigram index: §20 asks for searchable code
    fields and the volumes in **OQ-15** are ≤10⁵ Satnet Paths, where a sequential scan on a
    short column is not the thing that will be slow. The index is named in
    `docs/design/04` §6 and can be added without touching this.
    """
    return (
        Q(code__icontains=value) | Q(satnet__code__icontains=value) | Q(beam__code__icontains=value)
    )


def _beam(value: str) -> Q | None:
    return Q(beam__code__iexact=value)


def _satnet(value: str) -> Q | None:
    return Q(satnet__code__iexact=value)


def _valid_at(value: str) -> Q | None:
    """Allocations in force at one instant. **A-10** — the period is half-open.

    The single most useful filter on this table and the easiest to get subtly wrong: an
    allocation that ended at exactly the moment asked about is *not* in force, and one that
    has not ended is.
    """
    moment = _instant(value)
    if moment is None:
        return None
    return Q(valid_from__lte=moment) & (Q(valid_until__isnull=True) | Q(valid_until__gt=moment))


def _reserving(value: str) -> Q | None:
    """Only allocations that hold spectrum, or only those that do not. **A-12**."""
    if value == "yes":
        return Q(status__in=[PathStatus.PLANNED, PathStatus.PENDING_APPROVAL, PathStatus.ON_AIR])
    if value == "no":
        return ~Q(status__in=[PathStatus.PLANNED, PathStatus.PENDING_APPROVAL, PathStatus.ON_AIR])
    return None


def _instant(value: str) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        # **A-28**: a value with no zone is UTC. A `datetime-local` control submits one, and
        # guessing the reader's zone here would move every boundary on the page.
        from datetime import UTC

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


FILTERS: tuple[Filter, ...] = (
    Filter(
        "q",
        "Search",
        _text,
        help_text="Matches a Satnet Path, Satnet or Beam code.",
    ),
    Filter(
        "status",
        "Status",
        _status,
        choices=tuple((value, label) for value, label in PathStatus.choices),
    ),
    Filter("direction", "Direction", _direction, choices=(("FWD", "Forward"), ("RTN", "Return"))),
    Filter("beam", "Beam code", _beam),
    Filter("satnet", "Satnet code", _satnet),
    Filter(
        "valid_at",
        "In force at (UTC)",
        _valid_at,
        help_text="Allocations whose period covers this instant. Periods are half-open.",
    ),
    Filter(
        "reserving",
        "Holds spectrum",
        _reserving,
        choices=(("yes", "Yes"), ("no", "No")),
        help_text="Whether the status reserves spectrum (A-12).",
    ),
)

BY_KEY: dict[str, Filter] = {item.key: item for item in FILTERS}


def clean(parameters: Any) -> dict[str, str]:
    """The declared filters present in a request, with their raw values.

    Anything not in :data:`FILTERS` is discarded here, once, so nothing downstream has to
    remember to.
    """
    return {
        key: value.strip()
        for key, value in parameters.items()
        if key in BY_KEY and str(value).strip()
    }


def apply(queryset: QuerySet[SatnetPath], filters: dict[str, str]) -> QuerySet[SatnetPath]:
    """Narrow a queryset by cleaned filters, ignoring any that will not parse."""
    for key, value in filters.items():
        condition = BY_KEY[key].build(value)
        if condition is not None:
            queryset = queryset.filter(condition)
    return queryset


def describe(filters: dict[str, str]) -> list[str]:
    """What is currently applied, in words, for the table to show above itself.

    A table that is silently filtered is how somebody concludes an allocation has vanished.
    """
    return [f"{BY_KEY[key].label}: {value}" for key, value in sorted(filters.items())]
