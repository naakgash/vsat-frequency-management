"""UTC is the authoritative operational **and** display time zone. **OQ-23**, **A-28**.

The answer has two halves, and the second one is the one that needed building:

    *"All persisted timestamps, validity checks, overlap calculations, API values and audit
    records shall use UTC. Operational screens shall display UTC explicitly."*

Storage was already right — ``TIME_ZONE = "UTC"``, ``USE_TZ = True`` — and the tests below pin
it so a later convenience cannot quietly move it. *Explicit display* was not: Django renders a
timestamp in the active zone and prints no zone name at all, so a screen showing ``07:15`` was
telling every reader something slightly different and telling none of them which.

The last test is the one that matters most, and it is about what must **not** happen: a local
time zone may be offered as a secondary display, *"but it shall not affect validation or stored
values"*. Activating a zone per request would do exactly that, because the same activation that
formats an output also parses an input.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from beams.models import Beam
from operations.templatetags.utc_tags import utc
from satnet_paths.constants import InputMode, PathStatus
from satnet_paths.forms import SatnetPathForm
from satnet_paths.models import SatnetPath
from satnets import services as satnet_services
from tests.factories import make_admin
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement

MHZ = 1_000_000
ISTANBUL = ZoneInfo("Europe/Istanbul")
MOMENT = datetime.datetime(2026, 8, 5, 7, 15, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Storage and validation
# ---------------------------------------------------------------------------
def test_the_platform_computes_in_utc():
    """§14.1 and the answer's first sentence, pinned.

    Not ceremony: ``TIME_ZONE`` is what every ``timezone.now()``, every range comparison and
    every audit row is written against, so changing it to a local zone would silently move
    every validity boundary in the product.
    """
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def test_the_filter_names_the_zone():
    assert utc(MOMENT) == "2026-08-05 07:15 UTC"


def test_the_filter_converts_rather_than_relabels():
    """A value arriving in another zone is *converted*, not stamped.

    The failure this rules out is the quiet one: printing the local clock face and appending
    "UTC" would be worse than printing nothing, because it reads as authoritative.
    """
    local = MOMENT.astimezone(ISTANBUL)

    assert local.hour == 10  # +03:00, so the clock face differs
    assert utc(local) == "2026-08-05 07:15 UTC"


def test_an_active_local_zone_changes_nothing():
    """**A-28**: a secondary display may exist, but it may not reach anything that matters.

    ``timezone.activate`` is what a per-user zone preference would use, and it would also
    change how a submitted ``datetime-local`` value is parsed. This asserts the display is
    independent of it, which is what makes the two halves separable at all.
    """
    timezone.activate(ISTANBUL)
    try:
        assert utc(MOMENT) == "2026-08-05 07:15 UTC"
    finally:
        timezone.deactivate()


def test_a_naive_value_is_read_as_utc_rather_than_guessed_at():
    assert utc(MOMENT.replace(tzinfo=None)) == "2026-08-05 07:15 UTC"


def test_an_absent_timestamp_renders_as_an_em_dash():
    assert utc(None) == "—"
    assert utc("") == "—"


def test_the_filter_takes_a_format():
    template = Template('{% load utc_tags %}{{ value|utc:"Y-m-d" }}')

    assert template.render(Context({"value": MOMENT})) == "2026-08-05 UTC"


# ---------------------------------------------------------------------------
# The rule about defaults
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_defaulted_effective_period_starts_now_and_is_not_rounded_to_midnight():
    """*"An `effective_from` value that defaults to the present shall use the current UTC
    instant and shall not be rounded back to midnight."*

    S11 made this choice and flagged it as a sharp edge pending the answer — a Beam created at
    09:00 whose period began at 00:00 would silently accept allocations from nine hours before
    it existed. The answer confirms the instant, so the flag comes off.
    """
    before = timezone.now()
    beam = Beam(code="TZ-1", name="Zone", satellite_id=None, band_id=None)

    assert before <= beam.effective_from <= timezone.now()


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_wizard_labels_its_time_fields_utc():
    """The ``datetime-local`` widget shows no zone, so the label has to."""
    form = SatnetPathForm()

    assert form.fields["valid_from"].label.endswith("(UTC)")
    assert "UTC" in form.fields["valid_until"].help_text


@pytest.mark.django_db
def test_a_submitted_time_is_stored_as_the_utc_instant(client, world):
    """The half of the answer that is about *input*.

    A ``datetime-local`` field submits a wall-clock string with no zone. What the platform does
    with it is the whole question, and the answer is: reads it as UTC and stores that instant.
    """
    client.force_login(world["admin"])

    client.post(
        reverse("satnet_paths:create", kwargs={"satnet_pk": world["satnet"].pk}),
        {
            "code": "TZ-1",
            "direction": "FWD",
            "status": PathStatus.PLANNED,
            "input_mode": InputMode.OCCUPIED_BW,
            "input_value": 10 * MHZ,
            "rolloff": "0.2",
            "canonical_center_hz": 50 * MHZ,
            "valid_from": "2026-08-05T12:00",
            "gateway": "",
            "decimator_assignment": "",
        },
    )

    path = SatnetPath.objects.get()
    assert path.valid_from == datetime.datetime(2026, 8, 5, 12, 0, tzinfo=datetime.UTC)


@pytest.mark.django_db
def test_an_operational_screen_names_the_zone(client, world):
    """End to end: the value an operator reads says what it is."""
    client.force_login(world["admin"])
    path = SatnetPath.objects.create(
        **_minimum_path_fields(world), valid_from=timezone.now() - timezone.timedelta(days=1)
    )

    response = client.get(reverse("satnet_paths:detail", kwargs={"pk": path.pk}))

    assert "UTC" in response.content.decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def world(db):
    from beams import services as beam_services

    setup = make_entitlement(code="TZ", start_hz=0, end_hz=100 * MHZ)
    admin = make_admin()
    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    satnet = satnet_services.create(
        actor=admin,
        values={
            "code": "SN-TZ",
            "name": "Zones",
            "beam": setup.beam,
            "hub": make_hub(make_gateway("GW-TZ"), "HUB-TZ"),
            "effective_from": timezone.now() - timezone.timedelta(days=1),
        },
    )
    return {"setup": setup, "satnet": satnet, "admin": admin}


def _minimum_path_fields(world) -> dict:
    """Enough of a Path to render its detail screen, written directly.

    The service is exercised in its own tests; here the screen is the subject, and building the
    row directly keeps this test failing for display reasons only.
    """
    setup = world["setup"]
    assignment = setup.assignment
    return {
        "code": "TZ-SHOW",
        "satnet": world["satnet"],
        "beam": setup.beam,
        "direction": "FWD",
        "status": PathStatus.PLANNED,
        "input_mode": InputMode.OCCUPIED_BW,
        "input_value": 10 * MHZ,
        "rolloff": "0.2",
        "occupied_bw_hz": 10 * MHZ,
        "allocated_bw_hz": 10 * MHZ,
        "canonical_leg": "HUB_UPLINK",
        "canonical_window": setup.config.uplink_window,
        "canonical_assignment": assignment,
        "canonical_center_hz": 50 * MHZ,
        "canonical_occupied_start_hz": 45 * MHZ,
        "canonical_occupied_end_hz": 55 * MHZ,
        "canonical_allocated_start_hz": 45 * MHZ,
        "canonical_allocated_end_hz": 55 * MHZ,
        "canonical_polarization": setup.config.uplink_window.polarization,
        "translated_leg": "REMOTE_DOWNLINK",
        "translated_window": setup.config.downlink_window,
        "translated_assignment": setup.config.spectrum_assignments.get(
            frequency_window=setup.config.downlink_window
        ),
        "translated_center_hz": 19_050 * MHZ,
        "translated_occupied_start_hz": 19_045 * MHZ,
        "translated_occupied_end_hz": 19_055 * MHZ,
        "translated_allocated_start_hz": 19_045 * MHZ,
        "translated_allocated_end_hz": 19_055 * MHZ,
        "translated_polarization": setup.config.downlink_window.polarization,
    }
