"""Object-level scope for Gateways and Hubs — the tests that settle OQ-30.

Specification section 6 and design assumption A-17. Two questions were open:

* does a Gateway grant cascade to that Gateway's Hubs?  **Yes.**
* does a Hub grant imply its Gateway?  **No.**

Both are asserted here, including the case that matters operationally: a hub commissioned
*after* the grant is covered without anyone remembering to add a second grant.
"""

from __future__ import annotations

import pytest

from accounts import scope
from accounts.models import UserGatewayScope, UserHubScope
from inventory.models import Gateway, Hub
from tests.factories import make_admin, make_observer, make_operator
from tests.inventory.factories import make_gateway, make_hub


@pytest.mark.django_db
def test_a_user_with_no_grants_sees_nothing():
    """Deny by default (A-17)."""
    operator = make_operator()
    make_hub(make_gateway("GW-A"), "HUB-A")

    assert Gateway.objects.for_user(operator).count() == 0
    assert Hub.objects.for_user(operator).count() == 0


@pytest.mark.django_db
def test_a_gateway_grant_reveals_that_gateway():
    operator = make_operator()
    granted = make_gateway("GW-A")
    make_gateway("GW-B")
    UserGatewayScope.objects.create(user=operator, gateway=granted)

    visible = list(Gateway.objects.for_user(operator))

    assert visible == [granted]


@pytest.mark.django_db
def test_a_gateway_grant_cascades_to_its_hubs():
    """OQ-30, answered: granting a site should not require listing every hub at it."""
    operator = make_operator()
    gateway = make_gateway("GW-A")
    hub_one = make_hub(gateway, "HUB-1")
    hub_two = make_hub(gateway, "HUB-2")
    make_hub(make_gateway("GW-B"), "HUB-3")
    UserGatewayScope.objects.create(user=operator, gateway=gateway)

    visible = set(Hub.objects.for_user(operator).values_list("code", flat=True))

    assert visible == {hub_one.code, hub_two.code}


@pytest.mark.django_db
def test_the_cascade_covers_a_hub_commissioned_after_the_grant():
    """The operational reason the cascade exists.

    Without it, every new hub would silently be invisible to the people who run its site
    until someone remembered to issue a second grant.
    """
    operator = make_operator()
    gateway = make_gateway("GW-A")
    UserGatewayScope.objects.create(user=operator, gateway=gateway)

    later = make_hub(gateway, "HUB-NEW")

    assert scope.is_in_scope(operator, later) is True
    assert list(Hub.objects.for_user(operator)) == [later]


@pytest.mark.django_db
def test_a_hub_grant_does_not_imply_its_gateway():
    """A hub-level grant is narrower than a site-level one.

    Widening it would hand out access to every other hub at the site, which nobody
    granted.
    """
    operator = make_operator()
    gateway = make_gateway("GW-A")
    granted_hub = make_hub(gateway, "HUB-1")
    other_hub = make_hub(gateway, "HUB-2")
    UserHubScope.objects.create(user=operator, hub=granted_hub)

    assert list(Hub.objects.for_user(operator)) == [granted_hub]
    assert scope.is_in_scope(operator, other_hub) is False
    assert Gateway.objects.for_user(operator).count() == 0
    assert scope.is_in_scope(operator, gateway) is False


@pytest.mark.django_db
def test_direct_and_cascaded_grants_do_not_duplicate_rows():
    """A hub granted directly *and* through its Gateway must appear once."""
    operator = make_operator()
    gateway = make_gateway("GW-A")
    hub = make_hub(gateway, "HUB-1")
    UserGatewayScope.objects.create(user=operator, gateway=gateway)
    UserHubScope.objects.create(user=operator, hub=hub)

    assert Hub.objects.for_user(operator).count() == 1


@pytest.mark.django_db
def test_admin_bypasses_scope_without_any_grant():
    admin = make_admin()
    make_hub(make_gateway("GW-A"), "HUB-1")
    make_hub(make_gateway("GW-B"), "HUB-2")

    assert Gateway.objects.for_user(admin).count() == 2
    assert Hub.objects.for_user(admin).count() == 2


@pytest.mark.django_db
def test_an_observer_is_scoped_like_any_other_non_admin():
    """Read-only does not mean read-everything."""
    observer = make_observer()
    make_gateway("GW-A")

    assert Gateway.objects.for_user(observer).count() == 0


@pytest.mark.django_db
def test_anonymous_sees_nothing():
    from django.contrib.auth.models import AnonymousUser

    make_gateway("GW-A")

    assert Gateway.objects.for_user(AnonymousUser()).count() == 0


@pytest.mark.django_db
def test_the_resolvers_are_registered():
    """Registration happens in InventoryConfig.ready(). If it silently stopped, every
    scoped object would be treated as unscoped — the failure mode the forward guard in
    tests/permissions/test_scope_registry.py exists to catch."""
    assert scope.is_scoped(Gateway)
    assert scope.is_scoped(Hub)


@pytest.mark.django_db
def test_out_of_scope_detail_returns_404_not_403(client):
    """docs/design/03 section 3.3: the existence of an object outside a user's scope is
    itself information."""
    from tests.factories import TEST_PASSWORD

    make_operator("planner")
    gateway = make_gateway("GW-SECRET")
    client.login(username="planner", password=TEST_PASSWORD)

    response = client.get(gateway.get_absolute_url())

    assert response.status_code == 404
