"""The audit screens. §18, §26.17, §26.19.

`test_immutability.py` proves nothing here can be changed. This file is about the other half of
§18: that what was recorded can be *found*, and that finding it does not become a way to read
things you could not otherwise see.

Three properties.

**Visibility is a queryset, not a check after the fetch.** An Operator sees their own actions, an
administrator sees everything, and an Observer sees nothing at all. An event outside somebody's
visibility is a 404 rather than a 403, because "this event exists" is itself something the trail
should not disclose.

**There is no write route.** Not one that is protected — one that does not exist. The last test
enumerates the module's URL patterns and fails on any view that accepts an unsafe method.

**`audit` imports no other local module.** The screens are inside the module at the bottom of the
dependency graph, so they authorise and record without calling up into `accounts`. That is
asserted structurally, because it is the kind of thing a convenient import undoes in one line.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from audit import constants, selectors, services
from audit.models import AuditEvent
from tests.factories import make_admin, make_approver, make_observer, make_operator

pytestmark = pytest.mark.django_db

PATH_TYPE = "satnet_paths.SatnetPath"


@pytest.fixture
def trail(seeded_roles):
    """A small trail with two authors and one object, which is enough for every question here."""
    admin = make_admin("au-admin")
    operator = make_operator("au-operator")
    subject = uuid.uuid4()

    operator_event = services.record(
        action="SATNET_PATH_CREATED",
        actor=operator,
        object_type=PATH_TYPE,
        object_id=subject,
        object_repr="SP-1",
        after={"code": "SP-1", "status": "DRAFT", "occupied_bw_hz": 10_000_000},
        message="Created Satnet Path SP-1",
    )
    admin_event = services.record(
        action="SATNET_PATH_UPDATED",
        actor=admin,
        object_type=PATH_TYPE,
        object_id=subject,
        object_repr="SP-1",
        before={"code": "SP-1", "status": "DRAFT", "occupied_bw_hz": 10_000_000},
        after={"code": "SP-1", "status": "PLANNED", "occupied_bw_hz": 12_000_000},
        change_reason="widened",
        message="Changed Satnet Path SP-1",
    )
    return {
        "admin": admin,
        "operator": operator,
        "subject": subject,
        "operator_event": operator_event,
        "admin_event": admin_event,
    }


# ---------------------------------------------------------------------------
# Visibility — docs/design/03 §2.1
# ---------------------------------------------------------------------------
def test_an_administrator_sees_every_action(trail):
    assert selectors.visible(trail["admin"]).count() == AuditEvent.objects.count()


def test_an_operator_sees_only_their_own_actions(trail):
    visible = list(selectors.visible(trail["operator"]))

    assert [event.pk for event in visible] == [trail["operator_event"].pk]


def test_an_approver_sees_only_their_own_actions(trail):
    approver = make_approver("au-approver")

    assert not selectors.visible(approver).exists()


def test_an_observer_sees_nothing(trail):
    """Deliberate, and the one role whose exclusion is worth stating.

    An Observer reads operational data all day; a security trail is a different thing, and
    `docs/design/03` §2.1 marks the row "—" for them.
    """
    assert not selectors.visible(make_observer("au-observer")).exists()


def test_an_anonymous_reader_sees_nothing(trail):
    from django.contrib.auth.models import AnonymousUser

    assert not selectors.visible(AnonymousUser()).exists()


def test_a_filter_can_only_narrow_what_is_visible(trail):
    """The property that makes a shared search link safe to paste."""
    found = selectors.search(trail["operator"], {"actor": "au-admin"})

    assert not found.exists()


# ---------------------------------------------------------------------------
# Search — §18
# ---------------------------------------------------------------------------
def test_search_by_actor(trail):
    found = selectors.search(trail["admin"], {"actor": "au-operator"})

    assert [event.pk for event in found] == [trail["operator_event"].pk]


def test_search_by_actor_ignores_case(trail):
    assert selectors.search(trail["admin"], {"actor": "AU-Operator"}).count() == 1


def test_search_by_action(trail):
    assert selectors.search(trail["admin"], {"action": "satnet_path_updated"}).count() == 1


def test_search_by_object(trail):
    found = selectors.search(
        trail["admin"], {"object_type": PATH_TYPE, "object_id": str(trail["subject"])}
    )

    assert found.count() == 2


def test_search_by_outcome(trail):
    services.record(
        action=constants.PERMISSION_DENIED,
        actor=trail["operator"],
        outcome=constants.AuditOutcome.FAILURE,
        message="Denied",
    )

    assert selectors.search(trail["admin"], {"outcome": "FAILURE"}).count() == 1


def test_search_by_period(trail):
    now = timezone.now()
    since = (now - datetime.timedelta(minutes=5)).isoformat()
    until = (now + datetime.timedelta(minutes=5)).isoformat()

    assert selectors.search(trail["admin"], {"since": since, "until": until}).count() == 2
    assert not selectors.search(trail["admin"], {"since": until}).exists()


def test_a_period_boundary_includes_the_event_that_landed_on_it(trail):
    """Not half-open, unlike a validity period, and the docstring in `_until` says why."""
    moment = trail["admin_event"].occurred_at.isoformat()

    assert (
        selectors.search(trail["admin"], {"until": moment})
        .filter(pk=trail["admin_event"].pk)
        .exists()
    )


def test_search_by_import_batch(trail):
    """ "What did that import actually change" — the question S15 made askable."""
    batch = uuid.uuid4()
    services.record(
        action="SATNET_PATH_CREATED",
        actor=trail["admin"],
        message="Imported",
        import_batch_id=batch,
    )

    assert selectors.search(trail["admin"], {"batch": str(batch)}).count() == 1


def test_search_by_request(trail, client):
    """One save writes several events; the request id is what ties them together.

    Driven through a real request rather than by writing a row, because the id is stamped by
    the middleware and a test that supplied one itself would prove nothing about whether
    anything ever does.
    """
    from tests.factories import TEST_PASSWORD

    client.post(
        reverse("accounts:login"),
        {"username": trail["operator"].username, "password": TEST_PASSWORD},
    )
    signed_in = AuditEvent.objects.filter(action=constants.USER_LOGGED_IN).get()

    assert signed_in.request_id is not None
    found = selectors.search(trail["admin"], {"request": str(signed_in.request_id)})
    assert [event.pk for event in found] == [signed_in.pk]


def test_an_undeclared_parameter_never_reaches_the_orm(trail):
    """The parameters come from a URL, and a URL is user input."""
    cleaned = selectors.clean({"actor": "au-admin", "actor__isnull": "False", "before": "{}"})

    assert cleaned == {"actor": "au-admin"}


def test_an_unparseable_filter_is_dropped_rather_than_fatal(trail):
    """A hand-edited URL is routine; a 500 on one is not."""
    assert selectors.search(trail["admin"], {"object_id": "not-a-uuid"}).count() == 2
    assert selectors.search(trail["admin"], {"since": "yesterday"}).count() == 2


def test_the_action_list_comes_from_the_data(trail):
    """A form offering codes nothing has emitted teaches people the filter is broken."""
    assert set(selectors.actions_seen(trail["admin"])) == {
        "SATNET_PATH_CREATED",
        "SATNET_PATH_UPDATED",
    }


def test_the_action_list_is_narrowed_like_everything_else(trail):
    assert selectors.actions_seen(trail["operator"]) == ["SATNET_PATH_CREATED"]


# ---------------------------------------------------------------------------
# Field-level differences — §18
# ---------------------------------------------------------------------------
def test_only_changed_fields_appear_in_a_difference(trail):
    event = trail["admin_event"]

    changes = services.diff(event.before, event.after)

    assert set(changes) == {"status", "occupied_bw_hz"}
    assert changes["status"] == {"before": "DRAFT", "after": "PLANNED"}


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ({"a": 1}, {"a": 1}, set()),
        ({}, {"a": 1}, {"a"}),
        ({"a": 1}, {}, {"a"}),
        ({"rolloff": "0.20"}, {"rolloff": "0.35"}, {"rolloff"}),
    ],
)
def test_a_difference_is_computed_the_same_way_whatever_the_entity(before, after, expected):
    """One diff function, so a Satnet Path, a Beam and a user all read alike on the screen."""
    assert set(services.diff(before, after)) == expected


def test_a_created_record_shows_every_field_as_new(trail):
    changes = services.diff({}, trail["operator_event"].after)

    assert set(changes) == {"code", "status", "occupied_bw_hz"}
    assert all(change["before"] is None for change in changes.values())


# ---------------------------------------------------------------------------
# Object history — §26.17
# ---------------------------------------------------------------------------
def test_a_history_reads_oldest_first(trail):
    events = list(selectors.history_of(trail["admin"], PATH_TYPE, trail["subject"]))

    assert [event.pk for event in events] == [
        trail["operator_event"].pk,
        trail["admin_event"].pk,
    ]


def test_a_history_is_narrowed_to_what_the_reader_may_see(trail):
    events = list(selectors.history_of(trail["operator"], PATH_TYPE, trail["subject"]))

    assert [event.pk for event in events] == [trail["operator_event"].pk]


def test_a_history_survives_the_record_it_describes(trail):
    """Audit rows outlive what they describe, so the history never joins to the object."""
    events = list(selectors.history_of(trail["admin"], PATH_TYPE, uuid.uuid4()))

    assert events == []


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------
def test_an_administrator_can_search(trail, client):
    client.force_login(trail["admin"])

    response = client.get(reverse("audit:search"))

    assert response.status_code == 200
    assert b"SATNET_PATH_CREATED" in response.content
    assert b"SATNET_PATH_UPDATED" in response.content


def test_an_operator_sees_only_their_own_on_the_screen(trail, client):
    client.force_login(trail["operator"])

    response = client.get(reverse("audit:search"))

    assert response.status_code == 200
    assert b"SATNET_PATH_CREATED" in response.content
    assert b"SATNET_PATH_UPDATED" not in response.content


def test_an_observer_is_refused_and_the_denial_is_recorded(trail, client):
    observer = make_observer("au-obs2")
    client.force_login(observer)

    assert client.get(reverse("audit:search")).status_code == 403
    assert AuditEvent.objects.filter(action=constants.PERMISSION_DENIED, actor=observer).exists()


def test_an_anonymous_reader_is_sent_to_sign_in_rather_than_audited(trail, client):
    """A redirect to the sign-in page is ordinary behaviour, not a refusal worth recording."""
    before = AuditEvent.objects.filter(action=constants.PERMISSION_DENIED).count()

    response = client.get(reverse("audit:search"))

    assert response.status_code == 302
    assert AuditEvent.objects.filter(action=constants.PERMISSION_DENIED).count() == before


def test_an_event_outside_visibility_is_a_404_not_a_403(trail, client):
    """ "This event exists" is itself something the trail should not disclose."""
    client.force_login(trail["operator"])

    response = client.get(reverse("audit:event", kwargs={"pk": trail["admin_event"].pk}))

    assert response.status_code == 404


def test_the_event_screen_shows_the_field_level_difference(trail, client):
    client.force_login(trail["admin"])

    response = client.get(reverse("audit:event", kwargs={"pk": trail["admin_event"].pk}))

    assert response.status_code == 200
    body = response.content.decode()
    assert "occupied_bw_hz" in body
    assert "PLANNED" in body
    assert "widened" in body


def test_the_history_screen_renders(trail, client):
    client.force_login(trail["admin"])

    response = client.get(
        reverse("audit:history", kwargs={"object_type": PATH_TYPE, "object_id": trail["subject"]})
    )

    assert response.status_code == 200
    assert b"SP-1" in response.content


def test_the_search_screen_says_whose_trail_it_is_showing(trail, client):
    """A page that silently held back two thirds of the events is how somebody concludes the
    platform lost them."""
    client.force_login(trail["operator"])
    assert b"Your own recorded actions" in client.get(reverse("audit:search")).content

    client.force_login(trail["admin"])
    assert b"Every recorded action" in client.get(reverse("audit:search")).content


def test_a_satnet_path_links_to_its_own_history(trail, client, make_path):
    from satnet_paths.constants import PathStatus

    path = make_path(PathStatus.DRAFT, code="AU-1")
    client.force_login(trail["admin"])

    response = client.get(reverse("satnet_paths:detail", kwargs={"pk": path.pk}))

    expected = reverse("audit:history", kwargs={"object_type": PATH_TYPE, "object_id": path.pk})
    assert expected.encode() in response.content


def test_the_navigation_offers_audit_to_the_roles_that_hold_it(trail, client):
    client.force_login(trail["operator"])
    assert reverse("audit:search").encode() in client.get(reverse("home")).content

    client.force_login(make_observer("au-obs3"))
    assert reverse("audit:search").encode() not in client.get(reverse("home")).content


def test_a_search_is_paginated(trail, client, monkeypatch):
    """**OQ-15** does not say how large this table gets, so a page bounds one request's work."""
    from audit import views

    monkeypatch.setattr(views, "PAGE_SIZE", 1)
    client.force_login(trail["admin"])

    response = client.get(reverse("audit:search"))

    assert response.status_code == 200
    assert b"Page 1 of 2" in response.content


def test_a_pager_link_keeps_the_search(trail, client, monkeypatch):
    """A pager that dropped the filters would show page two of a different question."""
    from audit import views

    monkeypatch.setattr(views, "PAGE_SIZE", 1)
    client.force_login(trail["admin"])

    response = client.get(reverse("audit:search"), {"object_type": PATH_TYPE})

    assert b"object_type=" in response.content


# ---------------------------------------------------------------------------
# What must not exist
# ---------------------------------------------------------------------------
def test_no_audit_route_accepts_an_unsafe_method():
    """Not a protected write route — no write route.

    §18 and **A-15** make an event immutable and a database trigger enforces it, but the
    absence of a route is the first line and the only one a reader can check by looking.
    """
    from audit import urls

    unsafe = {"POST", "PUT", "PATCH", "DELETE"}
    for pattern in urls.urlpatterns:
        view_class = getattr(pattern.callback, "view_class", None)
        assert view_class is not None, f"{pattern.name} is not a class-based view"
        allowed = {method.upper() for method in view_class.http_method_names}
        implemented = {method for method in allowed if hasattr(view_class, method.lower())}
        assert not (implemented & unsafe), f"{pattern.name} accepts {implemented & unsafe}"


def test_the_screens_do_not_make_audit_depend_on_anything(trail):
    """The contract `lint-imports` holds, asserted here too because it is the point of the design.

    `audit` is at the bottom of the graph because everything above records into it. A screen
    over it that imported `accounts` for a permission mixin would invert that in one line, and
    the module would stop being safe to import from anywhere.
    """
    import ast
    import pathlib

    forbidden = {
        "accounts",
        "approvals",
        "beams",
        "calculations",
        "imports_exports",
        "inventory",
        "operations",
        "reporting",
        "satnet_paths",
        "satnets",
        "specifications",
    }
    root = pathlib.Path(__file__).resolve().parents[2] / "audit"

    for source in root.rglob("*.py"):
        if "migrations" in source.parts:
            continue
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, (
                    f"{source.relative_to(root.parent)} imports {name}; audit is the lowest "
                    f"layer and may import no other local module."
                )


def test_the_trail_is_still_append_only_behind_the_screens(trail):
    """Belt and braces with `test_immutability.py`: the screens changed nothing about that."""
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.filter(pk=trail["admin_event"].pk).update(message="tampered")

    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.filter(pk=trail["admin_event"].pk).delete()
