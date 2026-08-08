"""Reading the trail. §18, §26.17.

**This module imports no other local module, and neither does anything else in `audit`.**
`docs/design/01` §1 puts audit at the bottom of the graph because everything records to it; a
screen over it must not be the thing that inverts that. So visibility is decided here, from the
user object Django hands over, rather than by calling up into `accounts.policy` — and the
denial is recorded through `audit.services`, which is already in this module.

Two rules decide what somebody sees, and they are the whole of §18's access model:

* **`view_all_auditevent`** — everything. Administrators only (`docs/design/03` §2.1).
* **`view_auditevent`** — *their own actions*. An Operator and an Approver hold this; the events
  they authored are the events they may read.

There is no third rule and no object scope. An Operator can only have authored events about
objects they were allowed to act on, so "own actions" already carries **A-17**'s conjunctive
grant with it — a scope filter here would be a check that can never fail.

**Every filter is declared**, for the reason `reporting.filters` gives: the parameters come from
a URL, and a filter layer that passed unknown keys to ``filter(**params)`` would let a visitor
query columns no screen offers. A value that will not parse is dropped rather than fatal.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_datetime

from audit.constants import AuditOutcome
from audit.models import AuditEvent

VIEW_AUDIT = "audit.view_auditevent"
VIEW_ALL_AUDIT = "audit.view_all_auditevent"

#: Everything a row shows without a second query. `actor` is the only relation there is.
ROW_RELATIONS = ("actor",)


@dataclasses.dataclass(frozen=True)
class Filter:
    """One thing the trail may be narrowed by."""

    key: str
    label: str
    #: Builds the ``Q`` for a non-empty value, or returns None to ignore it.
    build: Callable[[str], Q | None]
    choices: tuple[tuple[str, str], ...] = ()
    help_text: str = ""


def _actor(value: str) -> Q | None:
    """Who did it, by username.

    By username rather than by id because a username is what somebody has in front of them —
    it is in the row, in the sign-in screen and in the administration list. Exact and
    case-insensitive: a partial match across a table this size is a sequential scan, and
    `audit_actor_idx` is on the foreign key.
    """
    return Q(actor__username__iexact=value) | Q(actor_username__iexact=value)


def _action(value: str) -> Q | None:
    return Q(action=value.strip().upper())


def _outcome(value: str) -> Q | None:
    return Q(outcome=value) if value in AuditOutcome.values else None


def _object_type(value: str) -> Q | None:
    return Q(object_type__iexact=value)


def _object_id(value: str) -> Q | None:
    identifier = _identifier(value)
    return Q(object_id=identifier) if identifier else None


def _import_batch(value: str) -> Q | None:
    """Everything one import did. §17.1, and new in S16 because S15 gave it something to hold.

    The single most useful question about a migration — "what did that file actually change" —
    and it is one indexed query because every import service passes its batch id to
    ``services.record``.
    """
    identifier = _identifier(value)
    return Q(import_batch_id=identifier) if identifier else None


def _request(value: str) -> Q | None:
    """Everything one HTTP request did.

    A single save can write several events — a Satnet Path created, its reservations placed,
    its approval recorded — and the request id is what ties them together. Without it, reading
    a busy trail means correlating by timestamp and hoping.
    """
    identifier = _identifier(value)
    return Q(request_id=identifier) if identifier else None


def _since(value: str) -> Q | None:
    moment = _instant(value)
    return Q(occurred_at__gte=moment) if moment else None


def _until(value: str) -> Q | None:
    """Up to and including. Deliberately not half-open, unlike a validity period.

    **A-10**'s half-open rule is about periods that abut — one allocation ending exactly where
    the next begins. A search box is not that: somebody typing an end time means "up to then",
    and excluding an event that landed on the second would look like the trail had lost it.
    """
    moment = _instant(value)
    return Q(occurred_at__lte=moment) if moment else None


FILTERS: tuple[Filter, ...] = (
    Filter("actor", "Actor", _actor, help_text="Username, exactly."),
    Filter(
        "action", "Action", _action, help_text="A stable action code, e.g. SATNET_PATH_CREATED."
    ),
    Filter(
        "outcome",
        "Outcome",
        _outcome,
        choices=tuple((value, label) for value, label in AuditOutcome.choices),
    ),
    Filter(
        "object_type", "Object type", _object_type, help_text="For example satnet_paths.SatnetPath."
    ),
    Filter("object_id", "Object", _object_id, help_text="The identifier of one record."),
    Filter("since", "From (UTC)", _since),
    Filter("until", "To (UTC)", _until),
    Filter("request", "Request", _request, help_text="Everything one request did."),
    Filter("batch", "Import batch", _import_batch, help_text="Everything one import did."),
)

BY_KEY: dict[str, Filter] = {item.key: item for item in FILTERS}


def clean(parameters: Any) -> dict[str, str]:
    """The declared filters present in a request, with their raw values."""
    return {
        key: str(value).strip()
        for key, value in parameters.items()
        if key in BY_KEY and str(value).strip()
    }


def visible(user: Any) -> QuerySet[AuditEvent]:
    """The events this user may read. §18, `docs/design/03` §2.1.

    Nothing for somebody holding neither capability — including an Observer, whose whole job is
    reading operational data and who has no business reading a security trail.
    """
    if not getattr(user, "is_authenticated", False):
        return AuditEvent.objects.none()
    if user.has_perm(VIEW_ALL_AUDIT):
        return AuditEvent.objects.select_related(*ROW_RELATIONS)
    if user.has_perm(VIEW_AUDIT):
        return AuditEvent.objects.select_related(*ROW_RELATIONS).filter(actor=user)
    return AuditEvent.objects.none()


def search(user: Any, filters: dict[str, str] | None = None) -> QuerySet[AuditEvent]:
    """The trail, narrowed. Visibility is applied first and a filter can only reduce it."""
    queryset = visible(user)
    for key, value in (filters or {}).items():
        condition = BY_KEY[key].build(value)
        if condition is not None:
            queryset = queryset.filter(condition)
    return queryset.order_by("-occurred_at")


def history_of(user: Any, object_type: str, object_id: uuid.UUID) -> QuerySet[AuditEvent]:
    """Everything recorded about one record, oldest first. §18, §26.17.

    Ascending, unlike every other listing here: a history is read as a story, and a story that
    starts with the ending is a list of events rather than an account of what happened.
    """
    return (
        visible(user).filter(object_type=object_type, object_id=object_id).order_by("occurred_at")
    )


def actions_seen(user: Any, limit: int = 200) -> list[str]:
    """The action codes actually present, for the search form's list.

    Read from the data rather than from a registry of constants: each module declares its own
    (`audit.constants` says so), and a form offering codes nothing has ever emitted teaches
    people that the filter is broken.
    """
    return sorted(
        visible(user).values_list("action", flat=True).distinct().order_by("action")[:limit]
    )


def form_fields(filters: dict[str, str]) -> list[dict[str, Any]]:
    """The search form's controls, each already carrying its current value.

    Resolved here rather than looked up in the template, and for a layering reason as much as a
    tidiness one: a dictionary lookup in a Django template needs a custom filter, the one that
    exists lives in ``reporting.templatetags``, and `audit` may not depend on a module above it
    (`docs/design/01` §1). Handing the template a list it can iterate needs no filter at all.
    """
    return [
        {
            "key": item.key,
            "label": item.label,
            "choices": item.choices,
            "help_text": item.help_text,
            "value": filters.get(item.key, ""),
            #: What control to draw. Named rather than inferred in the template, so adding a
            #: filter does not mean editing a chain of ``{% if %}``.
            "control": _control_for(item),
        }
        for item in FILTERS
    ]


def _control_for(item: Filter) -> str:
    if item.choices:
        return "select"
    if item.key in {"since", "until"}:
        return "datetime"
    if item.key == "action":
        return "action"
    return "text"


def describe(filters: dict[str, str]) -> list[str]:
    """What is currently applied, in words.

    A trail that is silently filtered is how somebody concludes an event was never recorded.
    """
    return [f"{BY_KEY[key].label}: {value}" for key, value in sorted(filters.items())]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _identifier(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError):
        return None


def _instant(value: str) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    # **A-28**: a value with no zone is UTC. A `datetime-local` control submits one, and
    # guessing the reader's zone here would move the boundary of every search.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
