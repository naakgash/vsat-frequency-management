"""The §10.3 table: filters, sorting, columns, and the query count. §26.11.

The test that matters most here is the last one. A table is the page most likely to be opened
with two hundred rows on it, and the failure is not visible in any single-row test: the page
works, the numbers are right, and it runs a thousand queries. So the query count is asserted
against *row count*, which is the only formulation that catches an N+1 without pinning a
baseline that every unrelated change then has to update.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.constants import Role
from reporting import columns as column_registry
from reporting import filters as filter_registry
from reporting import selectors
from satnet_paths import lifecycle
from satnet_paths.constants import PathStatus
from tests.factories import make_user

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


def _codes(queryset) -> set[str]:
    return set(queryset.values_list("code", flat=True))


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_the_table_shows_everything_when_nothing_is_asked(lifecycle_world, make_path):
    make_path(PathStatus.DRAFT, code="T-1")
    make_path(PathStatus.PLANNED, code="T-2", centre=20 * MHZ)

    assert _codes(selectors.table(lifecycle_world["admin"])) == {"T-1", "T-2"}


def test_a_status_filter_narrows(lifecycle_world, make_path):
    make_path(PathStatus.DRAFT, code="T-1")
    make_path(PathStatus.PLANNED, code="T-2", centre=20 * MHZ)

    found = selectors.table(lifecycle_world["admin"], filters={"status": PathStatus.PLANNED})

    assert _codes(found) == {"T-2"}


def test_a_direction_filter_narrows(lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="T-1")

    assert _codes(selectors.table(lifecycle_world["admin"], filters={"direction": "FWD"})) == {
        "T-1"
    }
    assert _codes(selectors.table(lifecycle_world["admin"], filters={"direction": "RTN"})) == set()


def test_the_search_box_matches_a_path_a_satnet_or_a_beam(lifecycle_world, make_path):
    make_path(PathStatus.DRAFT, code="T-FIND")

    by_path = selectors.table(lifecycle_world["admin"], filters={"q": "t-find"})
    by_satnet = selectors.table(lifecycle_world["admin"], filters={"q": "SN-LC"})
    by_beam = selectors.table(lifecycle_world["admin"], filters={"q": "BEAM-LC"})

    assert _codes(by_path) == {"T-FIND"}
    assert _codes(by_satnet) == {"T-FIND"}
    assert _codes(by_beam) == {"T-FIND"}


def test_in_force_at_is_half_open(lifecycle_world, make_path):
    """**A-10**, on the filter an operator uses most.

    An allocation that ended at exactly the instant asked about is *not* in force. Getting this
    edge wrong shows the same allocation as live for one extra minute, which is precisely the
    kind of quiet error the half-open convention exists to prevent.
    """
    path = make_path(PathStatus.PLANNED, code="T-WINDOW")
    ends = path.valid_from + timezone.timedelta(days=2)
    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values={
            "code": path.code,
            "direction": path.direction,
            "input_mode": path.input_mode,
            "input_value": path.input_value,
            "rolloff": path.rolloff,
            "guard_policy": None,
            "canonical_center_hz": path.canonical_center_hz,
            "valid_from": path.valid_from,
            "valid_until": ends,
        },
        expected_version=path.record_version,
    )
    admin = lifecycle_world["admin"]

    inside = selectors.table(admin, filters={"valid_at": (path.valid_from).isoformat()})
    on_the_end = selectors.table(admin, filters={"valid_at": ends.isoformat()})
    after = selectors.table(
        admin, filters={"valid_at": (ends + timezone.timedelta(minutes=1)).isoformat()}
    )

    assert _codes(inside) == {"T-WINDOW"}
    assert _codes(on_the_end) == set()
    assert _codes(after) == set()


def test_holding_spectrum_is_a_filter(lifecycle_world, make_path):
    """**A-12** as a question an operator actually asks: what is taking up spectrum?"""
    make_path(PathStatus.DRAFT, code="T-DRAFT")
    make_path(PathStatus.PLANNED, code="T-LIVE", centre=20 * MHZ)
    admin = lifecycle_world["admin"]

    assert _codes(selectors.table(admin, filters={"reserving": "yes"})) == {"T-LIVE"}
    assert _codes(selectors.table(admin, filters={"reserving": "no"})) == {"T-DRAFT"}


def test_an_unparseable_filter_is_ignored_rather_than_fatal(lifecycle_world, make_path):
    """A saved view outlives the status it names, and a hand-edited URL is routine."""
    make_path(PathStatus.DRAFT, code="T-1")

    found = selectors.table(
        lifecycle_world["admin"], filters={"status": "NOT_A_STATUS", "valid_at": "yesterday"}
    )

    assert _codes(found) == {"T-1"}


def test_an_undeclared_parameter_never_reaches_the_orm(lifecycle_world):
    """The filter layer is a whitelist. Passing unknown keys through to ``filter(**params)``
    would let a visitor query columns no screen offers — including ones scope hides."""
    cleaned = filter_registry.clean({"status": "DRAFT", "beam__satellite__code": "SAT-1", "q": ""})

    assert cleaned == {"status": "DRAFT"}


# ---------------------------------------------------------------------------
# Sorting and columns
# ---------------------------------------------------------------------------
def test_sorting_by_a_column_orders_by_its_field(lifecycle_world, make_path):
    make_path(PathStatus.DRAFT, code="T-B")
    make_path(PathStatus.DRAFT, code="T-A", centre=20 * MHZ)

    ascending = list(selectors.table(lifecycle_world["admin"], sort="code"))
    descending = list(selectors.table(lifecycle_world["admin"], sort="-code"))

    assert [p.code for p in ascending] == ["T-A", "T-B"]
    assert [p.code for p in descending] == ["T-B", "T-A"]


def test_an_unknown_sort_falls_back_rather_than_raising():
    """The sort arrives from a URL, and a URL is user input."""
    assert column_registry.ordering_for("nonsense") == ["satnet__code", "code"]
    assert column_registry.ordering_for("-nonsense") == ["satnet__code", "code"]


def test_a_column_that_cannot_be_sorted_says_so():
    for column in column_registry.COLUMNS:
        if not column.sortable:
            assert column_registry.ordering_for(column.key) == ["satnet__code", "code"]


def test_the_default_columns_are_a_readable_subset():
    """A default that showed everything would teach an operator to ignore the column picker."""
    assert 0 < len(column_registry.DEFAULT_KEYS) < len(column_registry.COLUMNS)


def test_an_unknown_column_in_a_saved_view_is_dropped_not_fatal():
    """A view outlives the column it names."""
    resolved = column_registry.resolve(["code", "a_column_that_was_renamed"])

    assert [column.key for column in resolved] == ["code"]


def test_resolving_nothing_gives_the_default():
    assert [c.key for c in column_registry.resolve([])] == list(column_registry.DEFAULT_KEYS)


def test_every_column_takes_its_heading_from_one_place():
    """§2 forbids the same description living in two places. A column names a dictionary code
    *or* carries its own label, and the dataclass refuses both or neither."""
    for column in column_registry.COLUMNS:
        assert bool(column.spec_code) != bool(column.label)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
def test_the_table_applies_the_same_read_scope_as_every_other_listing(lifecycle_world, make_path):
    """`docs/design/03` §4, and the S11 decision it rests on.

    **Reading a Satnet Path is open to any authenticated user; acting on one is not.** An
    Approver deciding on an allocation and an Observer reporting on one both need to read Paths
    they hold no grant for, so `satnet_paths.scope.visible_to` is deliberately permissive and
    the write question is asked separately (**A-17**).

    What the table must not do is *widen* that. It goes through the same selector, so an
    anonymous caller sees nothing and a signed-in one sees exactly what the Path list would
    have shown them.
    """
    from django.contrib.auth.models import AnonymousUser

    make_path(PathStatus.PLANNED, code="T-SCOPED")
    observer = make_user("t-observer", roles=[Role.OBSERVER])

    assert _codes(selectors.table(AnonymousUser())) == set()
    assert _codes(selectors.table(observer)) == {"T-SCOPED"}
    assert _codes(selectors.table(lifecycle_world["admin"])) == {"T-SCOPED"}


def test_the_table_shows_only_current_revisions(lifecycle_world, make_path):
    """§15.4. Older revisions are history; listing them would show one allocation twice."""
    path = make_path(PathStatus.PLANNED, code="T-REV")
    lifecycle.revise(
        actor=lifecycle_world["operator"],
        path=path,
        values={
            "code": path.code,
            "direction": path.direction,
            "input_mode": path.input_mode,
            "input_value": path.input_value,
            "rolloff": path.rolloff,
            "guard_policy": None,
            "canonical_center_hz": path.canonical_center_hz,
            "valid_until": None,
        },
        change_effective_at=timezone.now() + timezone.timedelta(days=1),
    )

    rows = list(selectors.table(lifecycle_world["admin"]))

    assert len(rows) == 1
    assert rows[0].revision_number == 2


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
def test_the_table_renders_for_every_role(client, lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="T-1")

    for role in (Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER):
        client.force_login(make_user(f"t-reader-{role}", roles=[role]))
        assert client.get(reverse("reporting:satnet-paths")).status_code == 200


def test_the_page_says_what_it_is_filtered_by(client, lifecycle_world, make_path):
    """A table that is silently narrowed is how somebody concludes an allocation has vanished."""
    make_path(PathStatus.PLANNED, code="T-1")
    client.force_login(lifecycle_world["admin"])

    response = client.get(reverse("reporting:satnet-paths"), {"status": PathStatus.PLANNED})

    assert "Status: PLANNED" in response.content.decode()


def test_chosen_columns_survive_into_the_page(client, lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="T-1")
    client.force_login(lifecycle_world["admin"])

    response = client.get(reverse("reporting:satnet-paths"), {"column": ["code", "gateway"]})

    assert [column.key for column in response.context["columns"]] == ["code", "gateway"]


def test_the_table_does_not_query_per_row(client, lifecycle_world, make_path):
    """The N+1 guard, asserted against row count rather than a fixed baseline.

    A row can reach its Satnet, Hub, Beam, Gateway and Decimator. Rendering those one row at a
    time is the difference between a page and an outage, and no single-row test would notice —
    which is why this one counts the queries for one row and then for three, with **every**
    column selected so that no relation is left unvisited.

    Comparing counts rather than pinning one is deliberate: a fixed number turns every
    unrelated middleware change into a failing test, and the thing worth protecting is the
    *slope*, not the intercept.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    make_path(PathStatus.PLANNED, code="T-1")
    client.force_login(lifecycle_world["admin"])
    url = reverse("reporting:satnet-paths")
    every_column = [column.key for column in column_registry.COLUMNS]

    with CaptureQueriesContext(connection) as first:
        client.get(url, {"column": every_column})

    make_path(PathStatus.PLANNED, code="T-2", centre=20 * MHZ)
    make_path(PathStatus.PLANNED, code="T-3", centre=35 * MHZ)

    with CaptureQueriesContext(connection) as third:
        client.get(url, {"column": every_column})

    assert len(third) == len(first), (
        f"Three rows cost {len(third)} queries where one cost {len(first)}. Something is being "
        f"fetched per row — check `reporting.selectors.ROW_RELATIONS`."
    )
