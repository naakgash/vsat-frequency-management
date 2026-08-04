"""The Engineering Preview screen. Specification sections 9.2, 9.4, 11.

Two things matter about this screen beyond its arithmetic: it must save nothing, and it
must show the formula behind every derived value rather than a bare number. Both are
requirements rather than niceties — §11 makes the backend result authoritative, and §9.4
requires derived values to be presented with their calculation.
"""

from __future__ import annotations

import pytest

from tests.factories import TEST_PASSWORD, make_observer

PREVIEW = "/engineering/preview/"


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


def _payload(**overrides) -> dict:
    payload = {
        "entry_mode": "SYMBOL_RATE",
        "symbol_rate_sps": "10000000",
        "occupied_bandwidth_hz": "",
        "rolloff": "0.35",
        "centre_hz": "29145",
        "guard_mode": "",
        "guard_fixed_left_hz": "",
        "guard_fixed_right_hz": "",
        "guard_percent_left": "",
        "guard_percent_right": "",
        "window_start_hz": "",
        "window_end_hz": "",
        "min_edge_guard_hz": "",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_any_authenticated_role_may_use_the_preview(client):
    """It reads nothing and writes nothing, so there is no data to scope and no capability
    to require beyond being signed in."""
    _sign_in(client, make_observer())

    assert client.get(PREVIEW).status_code == 200


@pytest.mark.django_db
def test_the_preview_requires_sign_in(client):
    response = client.get(PREVIEW)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_symbol_rate_produces_an_occupied_and_an_allocated_range(client):
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload())

    placement = response.context["placement"]
    assert response.status_code == 200
    assert placement.symbol_rate_sps == 10_000_000
    assert placement.occupied_bandwidth_hz == 13_500_000
    assert placement.centre_hz == 29_145_000_000


@pytest.mark.django_db
def test_an_occupied_bandwidth_produces_a_symbol_rate(client):
    """Section 9.2 requires both entry modes."""
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(entry_mode="OCCUPIED", symbol_rate_sps="", occupied_bandwidth_hz="13.5"),
    )

    assert response.context["placement"].symbol_rate_sps == 10_000_000


@pytest.mark.django_db
def test_the_unused_entry_field_is_ignored_rather_than_combined(client):
    """Only one of the two is editable at a time. A stale value in the other must not
    silently become the input."""
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(
            entry_mode="SYMBOL_RATE",
            symbol_rate_sps="10000000",
            occupied_bandwidth_hz="999",  # left over from the other mode
        ),
    )

    assert response.context["placement"].occupied_bandwidth_hz == 13_500_000


@pytest.mark.django_db
def test_a_missing_input_for_the_selected_mode_is_refused(client):
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload(symbol_rate_sps=""))

    assert response.status_code == 400
    assert "Required in this entry mode" in response.content.decode()


@pytest.mark.django_db
def test_a_percentage_guard_widens_the_allocated_range(client):
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(guard_mode="PERCENT_OF_OCCUPIED", guard_percent_left="5", guard_percent_right="5"),
    )

    placement = response.context["placement"]
    assert placement.guards.left_hz == 675_000
    assert placement.allocated_bandwidth_hz == 13_500_000 + 1_350_000


@pytest.mark.django_db
def test_a_guard_mode_without_its_values_is_refused(client):
    """Mirrors ck_guard_mode_has_required_values and GuardPolicySpec's own check."""
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload(guard_mode="FIXED"))

    assert response.status_code == 400
    assert "Required for the selected guard mode" in response.content.decode()


# ---------------------------------------------------------------------------
# Window containment
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_placement_outside_the_given_window_is_reported_as_blocking(client):
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload(window_start_hz="29000", window_end_hz="29100"))

    assert not response.context["is_placeable"]
    assert "OUTSIDE_WINDOW" in {f.code for f in response.context["findings"]}


@pytest.mark.django_db
def test_one_window_edge_without_the_other_is_refused(client):
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload(window_start_hz="29000"))

    assert response.status_code == 400
    assert "both edges" in response.content.decode()


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_derived_value_is_shown_with_the_formula_behind_it(client):
    """Section 9.4. An operator who can see how a figure was reached can tell a wrong input
    from a wrong tool; one who cannot has to trust it."""
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload())

    steps = response.context["steps"]
    assert [s["label"] for s in steps] == [
        "Occupied bandwidth",
        "Half width",
        "Occupied range",
        "Guards",
        "Allocated range",
    ]
    assert all(s["formula"] for s in steps)
    assert "symbol rate x (1 + roll-off), rounded up" in response.content.decode()


@pytest.mark.django_db
def test_frequencies_are_displayed_in_megahertz(client):
    """ADR-0003: stored and calculated in integer Hz, displayed in MHz, converted in one
    place."""
    _sign_in(client, make_observer())

    body = client.post(PREVIEW, _payload()).content.decode()

    assert "29,145.000" in body or "29,138.250" in body


@pytest.mark.django_db
def test_the_preview_writes_nothing(client):
    """No model, no audit record, no session state — which is why it needs no capability
    and no scope."""
    from audit.models import AuditEvent

    _sign_in(client, make_observer())
    before = AuditEvent.objects.count()

    client.post(PREVIEW, _payload())

    assert AuditEvent.objects.count() == before


# ---------------------------------------------------------------------------
# Both sides of the payload — S7
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_translation_produces_both_sides(client):
    """ADR-0006. The far side is the image of the entered side, not a second calculation."""
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(translation_method="OFFSET_SUBTRACT", translation_constant_hz="10000"),
    )

    both = response.context["both_sides"]
    assert both is not None
    assert both.uplink.occupied.start_hz == 29_138_250_000
    assert both.downlink.occupied.start_hz == 19_138_250_000
    assert both.widths_agree


@pytest.mark.django_db
def test_no_translation_leaves_the_preview_one_sided(client):
    """A payload translation is optional: the engine is useful before a Payload Path
    exists, which is most of why this screen was built this early."""
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload())

    assert response.context["both_sides"] is None


@pytest.mark.django_db
def test_a_method_without_a_constant_is_refused(client):
    """Translating by zero is not "no translation" — it is a downlink sitting exactly on
    top of its uplink, and it would look like a deliberate answer."""
    _sign_in(client, make_observer())

    response = client.post(PREVIEW, _payload(translation_method="OFFSET_ADD"))

    assert response.status_code == 400
    assert "Required when a translation method is selected" in response.content.decode()


@pytest.mark.django_db
def test_an_inverting_path_is_shown_as_inverting(client):
    """So a plot drawn left-to-right on both sides is not read the wrong way round."""
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(translation_method="LO_REFLECT", translation_constant_hz="49000"),
    )

    assert response.context["both_sides"].inverted
    assert "reverses the spectrum" in response.content.decode()


@pytest.mark.django_db
def test_a_path_flagged_as_inverting_without_a_reflection_is_reported(client):
    """The contradiction the engine cannot resolve, surfaced rather than guessed at."""
    _sign_in(client, make_observer())

    response = client.post(
        PREVIEW,
        _payload(
            translation_method="OFFSET_ADD",
            translation_constant_hz="1000",
            translation_inverts="on",
        ),
    )

    codes = {f.code for f in response.context["findings"]}
    assert "INVERSION_WITHOUT_REFLECTION" in codes
    assert not response.context["is_placeable"]
