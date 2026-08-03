"""The dependent-inventory screens, exercised over HTTP.

Every write is a direct POST rather than a submitted form, for the reason given in
``test_permissions.py``: a hidden button is not an enforced rule.

The versioning screens get most of the attention here because they are where a user can
do the wrong thing quietly — edit a record that operational data depends on, or read a
superseded definition as though it were current.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from audit.models import AuditEvent
from inventory import versioning
from inventory.constants import Direction, SpectrumLeg
from inventory.models import FrequencyWindow, GuardPolicy, PayloadPath
from tests.factories import TEST_PASSWORD, make_admin, make_observer, make_operator
from tests.inventory.factories import (
    make_band,
    make_frequency_window,
    make_guard_policy,
    make_payload_path,
    make_satellite,
)


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


def _stamp(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M")


def _window_payload(satellite, band, **overrides) -> dict:
    payload = {
        "code": "FW-NEW",
        "name": "New window",
        "satellite": str(satellite.pk),
        "band": str(band.pk),
        "side": SpectrumLeg.HUB_UPLINK,
        "polarization": "RHCP",
        "rf_start_hz": "29000",
        "rf_end_hz": "29500",
        "min_edge_guard_hz": "",
        "default_guard_policy": "",
        "effective_from": _stamp(timezone.now()),
        "effective_until": "",
        "source_reference": "",
        "description": "",
        "reason": "test",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/inventory/guard-policies/",
        "/inventory/frequency-windows/",
        "/inventory/payload-paths/",
    ],
)
def test_every_authenticated_role_may_read_the_new_lists(client, url):
    _sign_in(client, make_observer())

    assert client.get(url).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/inventory/guard-policies/",
        "/inventory/frequency-windows/",
        "/inventory/payload-paths/",
    ],
)
def test_the_new_lists_require_sign_in(client, url):
    response = client.get(url)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_a_window_detail_screen_shows_its_range_in_megahertz(client):
    """ADR-0003: stored as integer Hz, shown in MHz. No template does the arithmetic."""
    window = make_frequency_window(rf_start_hz=29_000_000_000, rf_end_hz=29_500_000_000)
    _sign_in(client, make_operator())

    body = client.get(window.get_absolute_url()).content.decode()

    assert "29,000.000" in body
    assert "29,500.000" in body
    # And the half-open convention is stated where someone reading the edges will see it.
    assert "[29,000.000, 29,500.000)" in body


@pytest.mark.django_db
def test_a_payload_path_detail_screen_names_both_window_sides(client):
    path = make_payload_path(direction=Direction.FWD)
    _sign_in(client, make_operator())

    body = client.get(path.get_absolute_url()).content.decode()

    assert "Hub uplink" in body
    assert "Remote downlink" in body
    assert path.uplink_window.code in body
    assert path.downlink_window.code in body


@pytest.mark.django_db
def test_the_window_list_shows_current_versions_only_until_asked(client):
    admin = make_admin()
    window = make_frequency_window(code="FW-HIST")
    versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    _sign_in(client, admin)

    current_only = client.get("/inventory/frequency-windows/")
    every_version = client.get("/inventory/frequency-windows/?versions=all")

    assert len(current_only.context["objects"]) == 1
    assert len(every_version.context["objects"]) == 2
    # Listing every version by default would show one logical window twice, with two
    # different frequency ranges and nothing saying which applies.
    assert current_only.context["objects"][0].version_number == 2


@pytest.mark.django_db
def test_the_index_counts_a_versioned_window_once(client):
    """Three versions of one window are one window, not three."""
    admin = make_admin()
    window = make_frequency_window()
    versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    _sign_in(client, admin)

    entries = {e["label"]: e["count"] for e in client.get("/inventory/").context["dependent"]}

    assert entries["Frequency Windows"] == 1
    assert FrequencyWindow.objects.count() == 2


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_create_a_window_by_direct_post(client, factory):
    satellite, band = make_satellite(), make_band()
    _sign_in(client, factory())

    response = client.post("/inventory/frequency-window/new/", _window_payload(satellite, band))

    assert response.status_code == 403
    assert FrequencyWindow.objects.count() == 0


@pytest.mark.django_db
def test_an_admin_creates_a_window_in_megahertz(client):
    satellite, band = make_satellite(), make_band()
    _sign_in(client, make_admin())

    response = client.post("/inventory/frequency-window/new/", _window_payload(satellite, band))

    assert response.status_code == 302
    window = FrequencyWindow.objects.get(code="FW-NEW")
    assert window.rf_start_hz == 29_000_000_000
    assert window.rf_end_hz == 29_500_000_000
    assert window.version_number == 1


@pytest.mark.django_db
def test_a_window_whose_start_is_above_its_end_is_refused_before_the_database(client):
    satellite, band = make_satellite(), make_band()
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/frequency-window/new/",
        _window_payload(satellite, band, rf_start_hz="29500", rf_end_hz="29000"),
    )

    assert response.status_code == 400
    assert "must be below" in response.content.decode()
    assert FrequencyWindow.objects.count() == 0


@pytest.mark.django_db
def test_a_guard_policy_missing_its_mode_values_is_refused_with_a_field_error(client):
    """Mirrors ``ck_guard_mode_has_required_values``; the database stays the authority."""
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/guard-policy/new/",
        {
            "code": "EMPTY",
            "name": "Fixed with no widths",
            "mode": "FIXED",
            "fixed_left_hz": "",
            "fixed_right_hz": "",
            "percent_left": "",
            "percent_right": "",
            "description": "",
            "reason": "test",
        },
    )

    assert response.status_code == 400
    assert "Required for the selected guard mode" in response.content.decode()
    assert GuardPolicy.objects.count() == 0


@pytest.mark.django_db
def test_a_path_whose_windows_contradict_its_direction_is_refused_with_a_field_error(client):
    """The form names the sides. The CHECK and the composite keys still stand behind it."""
    satellite = make_satellite()
    band = make_band()
    uplink = make_frequency_window(satellite, "UL", SpectrumLeg.HUB_UPLINK, band=band)
    downlink = make_frequency_window(
        satellite,
        "DL",
        SpectrumLeg.REMOTE_DOWNLINK,
        band=band,
        rf_start_hz=19_000_000_000,
        rf_end_hz=19_500_000_000,
    )
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/payload-path/new/",
        {
            "code": "PP-BAD",
            "name": "Return path with forward windows",
            "satellite": str(satellite.pk),
            "direction": Direction.RTN,
            "uplink_window": str(uplink.pk),
            "downlink_window": str(downlink.pk),
            "translation_method": "OFFSET_SUBTRACT",
            "translation_constant_hz": "10000",
            "effective_from": _stamp(timezone.now()),
            "effective_until": "",
            "engineering_reference": "",
            "description": "",
            "reason": "test",
        },
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert "takes its uplink from a REMOTE_UPLINK window" in body
    assert PayloadPath.objects.count() == 0


@pytest.mark.django_db
def test_the_side_columns_are_derived_and_not_accepted_from_the_request(client):
    """They exist so a composite key can prove the windows match the direction.

    Accepting them from the request would let the caller assert the very thing the
    columns are there to verify.
    """
    satellite = make_satellite()
    band = make_band()
    uplink = make_frequency_window(satellite, "UL", SpectrumLeg.HUB_UPLINK, band=band)
    downlink = make_frequency_window(
        satellite,
        "DL",
        SpectrumLeg.REMOTE_DOWNLINK,
        band=band,
        rf_start_hz=19_000_000_000,
        rf_end_hz=19_500_000_000,
    )
    _sign_in(client, make_admin())

    response = client.post(
        "/inventory/payload-path/new/",
        {
            "code": "PP-OK",
            "name": "Forward path",
            "satellite": str(satellite.pk),
            "direction": Direction.FWD,
            "uplink_window": str(uplink.pk),
            "downlink_window": str(downlink.pk),
            # Supplied by the caller and deliberately wrong. It must be ignored.
            "uplink_window_side": SpectrumLeg.REMOTE_UPLINK,
            "downlink_window_side": SpectrumLeg.HUB_DOWNLINK,
            "translation_method": "OFFSET_SUBTRACT",
            "translation_constant_hz": "10000",
            "effective_from": _stamp(timezone.now()),
            "effective_until": "",
            "engineering_reference": "",
            "description": "",
            "reason": "test",
        },
    )

    assert response.status_code == 302
    path = PayloadPath.objects.get(code="PP-OK")
    assert path.uplink_window_side == SpectrumLeg.HUB_UPLINK
    assert path.downlink_window_side == SpectrumLeg.REMOTE_DOWNLINK


# ---------------------------------------------------------------------------
# The bound form must not stand in for the stored row
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_edit_records_the_values_that_were_actually_replaced(client):
    """Regression. ``ModelForm._post_clean`` writes the submitted values onto the
    instance it was given, so a view that hands that same object to the service gives it
    an object already describing the change.

    The audit trail then reports the new values as the old ones, which is worse than no
    audit record at all: it looks authoritative and says nothing changed. The same
    aliasing silently disarmed the section 13.6 retroactive-edit guard, which is how this
    was found.
    """
    window = make_frequency_window(rf_end_hz=29_500_000_000)
    _sign_in(client, make_admin())

    client.post(
        f"/inventory/frequency-window/{window.pk}/edit/",
        _window_payload(
            window.satellite,
            window.band,
            code=window.code,
            name=window.name,
            side=window.side,
            rf_end_hz="29700",
            effective_from=_stamp(window.effective_from),
            expected_version=window.record_version,
        ),
    )

    event = AuditEvent.objects.get(action="INVENTORY_UPDATED")
    assert event.before["rf_end_hz"] == 29_500_000_000
    assert event.after["rf_end_hz"] == 29_700_000_000


# ---------------------------------------------------------------------------
# Versioning over HTTP
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_editing_an_in_use_window_is_refused_with_the_supported_route_named(client):
    """Specification section 13.6, delivered as a message rather than an error page."""
    path = make_payload_path()
    window = path.uplink_window
    _sign_in(client, make_admin())

    response = client.post(
        f"/inventory/frequency-window/{window.pk}/edit/",
        _window_payload(
            window.satellite,
            window.band,
            code=window.code,
            name=window.name,
            side=window.side,
            rf_end_hz="29600",
            effective_from=_stamp(window.effective_from),
            expected_version=window.record_version,
        ),
    )

    body = response.content.decode()
    assert response.status_code == 409
    assert "1 Payload Paths" in body
    assert "create a new version" in body
    window.refresh_from_db()
    assert window.rf_end_hz == 29_500_000_000


@pytest.mark.django_db
def test_superseding_a_window_over_http_closes_one_version_and_opens_the_next(client):
    window = make_frequency_window(rf_end_hz=29_500_000_000)
    changeover = timezone.now() + timedelta(days=1)
    _sign_in(client, make_admin())

    response = client.post(
        f"/inventory/frequency-window/{window.pk}/supersede/",
        _window_payload(
            window.satellite,
            window.band,
            code=window.code,
            name=window.name,
            side=window.side,
            rf_end_hz="29600",
            effective_from=_stamp(changeover),
            reason="Transponder re-plan",
        ),
    )

    assert response.status_code == 302
    window.refresh_from_db()
    successor = FrequencyWindow.objects.get(version_group=window.version_group, version_number=2)
    assert window.effective_until is not None
    assert window.rf_end_hz == 29_500_000_000
    assert successor.rf_end_hz == 29_600_000_000
    assert successor.effective_until is None
    assert successor.effective_from == window.effective_until

    event = AuditEvent.objects.get(action="MASTER_DATA_VERSIONED")
    assert event.change_reason == "Transponder re-plan"


@pytest.mark.django_db
def test_a_successor_dated_before_its_predecessor_is_refused_on_the_form(client):
    window = make_frequency_window()
    _sign_in(client, make_admin())

    response = client.post(
        f"/inventory/frequency-window/{window.pk}/supersede/",
        _window_payload(
            window.satellite,
            window.band,
            code=window.code,
            name=window.name,
            side=window.side,
            effective_from=_stamp(window.effective_from - timedelta(days=1)),
        ),
    )

    assert response.status_code == 400
    assert "must take effect after" in response.content.decode()
    assert FrequencyWindow.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_supersede(client, factory):
    window = make_frequency_window()
    _sign_in(client, factory())

    response = client.post(
        f"/inventory/frequency-window/{window.pk}/supersede/",
        _window_payload(window.satellite, window.band, code=window.code, side=window.side),
    )

    assert response.status_code == 403
    assert FrequencyWindow.objects.count() == 1


@pytest.mark.django_db
def test_a_denied_supersede_is_audited(client):
    window = make_frequency_window()
    operator = make_operator()
    _sign_in(client, operator)

    client.post(f"/inventory/frequency-window/{window.pk}/supersede/", {})

    assert AuditEvent.objects.filter(
        action="PERMISSION_DENIED", actor=operator, outcome="FAILURE"
    ).exists()


@pytest.mark.django_db
def test_the_version_history_screen_lists_every_version_oldest_first(client):
    admin = make_admin()
    window = make_frequency_window()
    second = versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    _sign_in(client, admin)

    response = client.get(f"/inventory/frequency-window/{second.pk}/versions/")

    assert response.status_code == 200
    assert [v.version_number for v in response.context["versions"]] == [1, 2]
    assert response.context["current"] == second


@pytest.mark.django_db
def test_a_superseded_window_says_so_and_points_at_the_current_one(client):
    """The failure this prevents is silent: reading old frequencies as though current."""
    admin = make_admin()
    window = make_frequency_window()
    successor = versioning.supersede(
        actor=admin, instance=window, values={}, effective_from=timezone.now() + timedelta(days=1)
    )
    window.refresh_from_db()
    _sign_in(client, admin)

    body = client.get(window.get_absolute_url()).content.decode()

    assert "superseded" in body.lower()
    assert successor.get_absolute_url() in body


@pytest.mark.django_db
def test_the_version_history_screen_is_readable_by_any_role(client):
    """Which definition an allocation was validated against is part of reading it."""
    window = make_frequency_window()
    _sign_in(client, make_observer())

    response = client.get(f"/inventory/frequency-window/{window.pk}/versions/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_version_screens_refuse_an_entity_that_is_not_versioned(client):
    """A Guard Policy has no versions; offering the screen would imply it does."""
    policy = make_guard_policy()
    _sign_in(client, make_admin())

    assert client.get(f"/inventory/guard-policy/{policy.pk}/versions/").status_code == 404
    assert client.get(f"/inventory/guard-policy/{policy.pk}/supersede/").status_code == 404


@pytest.mark.django_db
def test_the_activation_route_still_works_alongside_the_version_routes(client):
    """``versions`` and ``supersede`` sit in the same URL position as an activation
    action, and would be swallowed by it if the patterns were ordered the other way."""
    policy = make_guard_policy()
    _sign_in(client, make_admin())

    response = client.post(f"/inventory/guard-policy/{policy.pk}/deactivate/")

    assert response.status_code == 302
    policy.refresh_from_db()
    assert policy.is_active is False
