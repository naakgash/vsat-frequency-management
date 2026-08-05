"""Saved views and the dashboard. §10.3, §16, §26.11.

The load-bearing claim about a saved view is that **sharing one shares the question, never the
answer**: the table it produces is scope-filtered on every read, so a view an administrator
shares shows each reader only their own spectrum. That is what makes `is_shared` safe to have
at all, and it is asserted here rather than assumed.

The dashboard's claim is narrower and just as easy to lose: every figure on it is a query, not
a stored counter. §16 forbids a second source of truth for free capacity, and a cached count of
allocations would be the same mistake one table over.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts.constants import Role
from reporting import selectors, services
from reporting.models import SavedView
from satnet_paths.constants import PathStatus
from spectrum import selectors as spectrum_selectors
from tests.factories import make_user

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------
def test_a_view_stores_the_filters_and_columns_it_was_given(lifecycle_world):
    view = services.save(
        actor=lifecycle_world["operator"],
        name="Planned FWD",
        filters={"status": PathStatus.PLANNED, "direction": "FWD"},
        columns=["code", "status"],
        sort="-valid_from",
    )

    assert view.filters == {"status": PathStatus.PLANNED, "direction": "FWD"}
    assert view.columns == ["code", "status"]
    assert view.sort == "-valid_from"


def test_saving_re_cleans_what_it_is_given(lifecycle_world):
    """The filters arrive from a query string. A view that stored an unknown key would hand it
    to the table every time it was applied, forever."""
    view = services.save(
        actor=lifecycle_world["operator"],
        name="Suspicious",
        filters={"status": PathStatus.PLANNED, "beam__satellite__code": "SAT-1"},
        columns=["code", "not_a_column"],
    )

    assert view.filters == {"status": PathStatus.PLANNED}
    assert view.columns == ["code"]


def test_saving_the_same_name_twice_replaces_rather_than_fails(lifecycle_world):
    """ "Save" over an existing name is what somebody adjusting a view means by it."""
    services.save(
        actor=lifecycle_world["operator"],
        name="Mine",
        filters={"direction": "FWD"},
        columns=["code"],
    )
    services.save(
        actor=lifecycle_world["operator"],
        name="Mine",
        filters={"direction": "RTN"},
        columns=["code"],
    )

    assert SavedView.objects.count() == 1
    assert SavedView.objects.get().filters == {"direction": "RTN"}


def test_two_people_may_use_the_same_name(lifecycle_world):
    services.save(actor=lifecycle_world["operator"], name="Mine", filters={}, columns=["code"])
    services.save(actor=lifecycle_world["approver"], name="Mine", filters={}, columns=["code"])

    assert SavedView.objects.count() == 2


def test_a_private_view_is_invisible_to_everybody_else(lifecycle_world):
    services.save(actor=lifecycle_world["operator"], name="Private", filters={}, columns=["code"])

    assert selectors.views_for(lifecycle_world["operator"]).count() == 1
    assert selectors.views_for(lifecycle_world["approver"]).count() == 0


def test_a_shared_view_is_offered_to_everybody(lifecycle_world):
    services.save(
        actor=lifecycle_world["operator"],
        name="Shared",
        filters={},
        columns=["code"],
        is_shared=True,
    )

    assert selectors.views_for(lifecycle_world["approver"]).count() == 1


def test_a_shared_view_shares_the_question_and_not_the_answer(lifecycle_world, make_path):
    """The reason `is_shared` is safe. Applying somebody else's view runs the same
    scope-filtered query, so an anonymous reader gets nothing from an administrator's view."""
    from django.contrib.auth.models import AnonymousUser

    make_path(PathStatus.PLANNED, code="V-1")
    view = services.save(
        actor=lifecycle_world["admin"],
        name="Everything",
        filters={},
        columns=["code"],
        is_shared=True,
    )

    as_admin = selectors.table(lifecycle_world["admin"], filters=view.filters)
    as_nobody = selectors.table(AnonymousUser(), filters=view.filters)

    assert as_admin.count() == 1
    assert as_nobody.count() == 0


def test_a_view_reproduces_itself_as_a_query_string(lifecycle_world):
    """Applying a view is a redirect, not a second code path — so a shared link and a saved
    view cannot disagree about what they show."""
    view = services.save(
        actor=lifecycle_world["operator"],
        name="Planned",
        filters={"status": PathStatus.PLANNED},
        columns=["code", "status"],
        sort="code",
    )

    assert view.query_string == "status=PLANNED&column=code&column=status&sort=code"


def test_only_the_owner_may_delete_a_view(lifecycle_world):
    view = services.save(
        actor=lifecycle_world["operator"], name="Mine", filters={}, columns=["code"], is_shared=True
    )

    with pytest.raises(services.NotYours):
        services.delete(actor=lifecycle_world["approver"], view=view)

    assert SavedView.objects.count() == 1


def test_not_even_an_administrator_may_delete_somebody_elses_view(lifecycle_world):
    """A saved view is a personal working tool, not operational data. §20's "no hard deletes"
    is about the record of what was allocated — which this is not."""
    view = services.save(
        actor=lifecycle_world["operator"], name="Mine", filters={}, columns=["code"], is_shared=True
    )

    with pytest.raises(services.NotYours):
        services.delete(actor=lifecycle_world["admin"], view=view)


def test_the_owner_may_delete_their_own(lifecycle_world):
    view = services.save(
        actor=lifecycle_world["operator"], name="Mine", filters={}, columns=["code"]
    )

    services.delete(actor=lifecycle_world["operator"], view=view)

    assert SavedView.objects.count() == 0


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
def test_saving_a_view_from_the_table_and_getting_back_to_it(client, lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="V-1")
    client.force_login(lifecycle_world["operator"])

    response = client.post(
        reverse("reporting:save-view"),
        {"name": "Live FWD", "status": PathStatus.PLANNED, "direction": "FWD", "column": ["code"]},
    )

    view = SavedView.objects.get()
    assert view.name == "Live FWD"
    assert response.status_code == 302
    # The table's state survives the round trip, which is what makes "save" not also mean
    # "lose what I was looking at".
    assert "status=PLANNED" in response["Location"]


def test_a_view_without_a_name_is_refused_without_saving(client, lifecycle_world):
    client.force_login(lifecycle_world["operator"])

    client.post(reverse("reporting:save-view"), {"name": "  ", "column": ["code"]})

    assert SavedView.objects.count() == 0


def test_deleting_somebody_elses_view_over_http_is_forbidden(client, lifecycle_world):
    view = services.save(
        actor=lifecycle_world["operator"], name="Mine", filters={}, columns=["code"], is_shared=True
    )
    client.force_login(lifecycle_world["approver"])

    response = client.post(reverse("reporting:delete-view", kwargs={"pk": view.pk}))

    assert response.status_code == 403
    assert SavedView.objects.count() == 1


def test_every_role_may_save_a_view(client, lifecycle_world):
    """An Observer, whose whole job is reading tables, needs this most."""
    for role in (Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER):
        client.force_login(make_user(f"v-{role}", roles=[role]))
        response = client.post(
            reverse("reporting:save-view"), {"name": f"View {role}", "column": ["code"]}
        )
        assert response.status_code == 302

    assert SavedView.objects.count() == 4


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------
def test_the_dashboard_counts_match_the_table(lifecycle_world, make_path):
    """§16's rule, applied one table over: no figure on the dashboard is stored, and every one
    of them has to agree with the selector a reader can check it against."""
    make_path(PathStatus.DRAFT, code="D-1")
    make_path(PathStatus.PLANNED, code="D-2", centre=20 * MHZ)
    make_path(PathStatus.PENDING_APPROVAL, code="D-3", centre=35 * MHZ)
    admin = lifecycle_world["admin"]

    summary = selectors.dashboard(admin)

    assert summary.total_paths == selectors.table(admin).count()
    assert (
        summary.awaiting_approval
        == selectors.table(admin, filters={"status": PathStatus.PENDING_APPROVAL}).count()
    )
    counted = {entry.status: entry.count for entry in summary.statuses}
    assert counted[PathStatus.DRAFT] == 1
    assert counted[PathStatus.PLANNED] == 1
    assert counted[PathStatus.PENDING_APPROVAL] == 1


def test_every_status_appears_even_at_zero(lifecycle_world):
    """A status missing from the list reads as "not applicable" rather than "none yet"."""
    summary = selectors.dashboard(lifecycle_world["admin"])

    assert {entry.status for entry in summary.statuses} == set(PathStatus.values)
    assert summary.total_paths == 0


def test_the_reserving_total_follows_a12(lifecycle_world, make_path):
    make_path(PathStatus.DRAFT, code="D-1")
    make_path(PathStatus.PLANNED, code="D-2", centre=20 * MHZ)

    summary = selectors.dashboard(lifecycle_world["admin"])

    assert summary.reserving_paths == 1


def test_beam_utilisation_comes_from_the_gap_engine(lifecycle_world, make_path):
    """Not a second calculation. A dashboard that disagreed with a Beam's own page would be a
    difference of opinion between two implementations of §16."""
    make_path(PathStatus.PLANNED, code="D-1")
    config = lifecycle_world["setup"].config

    summary = selectors.dashboard(lifecycle_world["admin"])
    direct = spectrum_selectors.capacity(config, leg=config.canonical_leg)

    entry = next(e for e in summary.utilisation if e.beam == lifecycle_world["setup"].beam)
    assert entry.free_hz == direct.free_hz
    assert entry.total_hz == direct.total_hz
    assert entry.largest_gap_hz == direct.largest_gap_hz


def test_utilisation_is_bounded(lifecycle_world, make_path):
    """Free capacity is a query per Beam direction, so the dashboard shows the first few and
    the Beam list holds the rest. A bound on work, not a page size."""
    make_path(PathStatus.PLANNED, code="D-1")

    summary = selectors.dashboard(lifecycle_world["admin"], utilisation_limit=0)

    assert summary.utilisation == ()


def test_the_dashboard_renders(client, lifecycle_world, make_path):
    make_path(PathStatus.PLANNED, code="D-1")
    client.force_login(make_user("d-observer", roles=[Role.OBSERVER]))

    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert "Beam utilisation" in response.content.decode()


def test_the_dashboard_needs_a_session(client):
    """It shows scoped figures, so it is not a landing page any more."""
    response = client.get(reverse("home"))

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_a_percentage_is_a_whole_number(lifecycle_world, make_path):
    """It is a bar on a screen. A figure with decimal places would invite somebody to quote
    it; everything anything is *derived* from stays in integer Hz (**A-08**)."""
    make_path(PathStatus.PLANNED, code="D-1")

    summary = selectors.dashboard(lifecycle_world["admin"])

    for entry in summary.utilisation:
        assert isinstance(entry.percent_used, int)
        assert 0 <= entry.percent_used <= 100
