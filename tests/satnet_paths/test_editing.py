"""Editing in place, and the stale submission that must not be accepted. §15.4, §15.5.

Two rules, and the second is the interesting one.

**Fields are editable only in `DRAFT` and `PLANNED`** (`docs/design/03` §5). Anything approved,
on air or suspended changes through a revision, so that an allocation somebody signed off does
not silently become a different allocation under the same identity.

**A stale submission is refused with the differences, not with a shrug.** §15.5 asks for a
field-level comparison, and the reason is practical: "this record changed, reload" makes an
operator redo work they may not need to redo, and when the other edit touched a different field
entirely it makes them redo it for nothing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from satnet_paths import lifecycle
from satnet_paths.constants import InputMode, PathStatus
from spectrum.models import SpectrumReservation

pytestmark = pytest.mark.django_db

MHZ = 1_000_000


def _values(path, **extra):
    values = {
        "code": path.code,
        "direction": path.direction,
        "input_mode": path.input_mode,
        "input_value": path.input_value,
        "rolloff": path.rolloff,
        "guard_policy": path.guard_policy,
        "canonical_center_hz": path.canonical_center_hz,
        "valid_from": path.valid_from,
        "valid_until": None,
    }
    values.update(extra)
    return values


def _post(path, **extra):
    data = {
        "code": path.code,
        "direction": path.direction,
        "input_mode": path.input_mode,
        "input_value": path.input_value,
        "rolloff": str(path.rolloff),
        "canonical_center_hz": path.canonical_center_hz,
        "valid_from": path.valid_from.strftime("%Y-%m-%dT%H:%M"),
        "record_version": path.record_version,
        "gateway": "",
        "decimator_assignment": "",
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# §15.4 — where editing is allowed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [PathStatus.DRAFT, PathStatus.PLANNED])
def test_an_editable_allocation_can_be_changed(lifecycle_world, make_path, status):
    path = make_path(status)

    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=15 * MHZ),
        expected_version=path.record_version,
    )

    path.refresh_from_db()
    assert path.occupied_bw_hz == 15 * MHZ


@pytest.mark.parametrize(
    "status", [PathStatus.PENDING_APPROVAL, PathStatus.ON_AIR, PathStatus.SUSPENDED]
)
def test_an_allocation_under_review_or_on_air_is_not_edited_in_place(
    lifecycle_world, make_path, status
):
    path = make_path(status)

    with pytest.raises(lifecycle.NotEditable, match="new revision"):
        lifecycle.edit(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path, input_value=15 * MHZ),
            expected_version=path.record_version,
        )


def test_editing_rewrites_the_occupancy_rows(lifecycle_world, make_path):
    """The old rows are released before the new ones are written, in one transaction — the same
    ordering a revision needs, because an edit that keeps part of its own frequency would
    otherwise be refused by the rows it is replacing (**A-14**)."""
    path = make_path(PathStatus.PLANNED)
    rows = SpectrumReservation.objects.filter(satnet_path_id=path.pk)
    before = set(rows.values_list("id", flat=True))

    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=20 * MHZ),
        expected_version=path.record_version,
    )

    rows = SpectrumReservation.objects.filter(satnet_path_id=path.pk)
    assert rows.count() == 2
    assert not set(rows.values_list("id", flat=True)) & before
    assert rows.first().allocated_end_hz - rows.first().allocated_start_hz >= 20 * MHZ


def test_a_draft_edit_writes_no_reservations(lifecycle_world, make_path):
    """A draft holds nothing before the edit and nothing after it (**A-12**)."""
    path = make_path(PathStatus.DRAFT)

    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=15 * MHZ),
        expected_version=path.record_version,
    )

    assert not SpectrumReservation.objects.filter(satnet_path_id=path.pk).exists()


# ---------------------------------------------------------------------------
# §15.5 — the stale submission
# ---------------------------------------------------------------------------
def test_a_stale_submission_is_refused(lifecycle_world, make_path):
    path = make_path(PathStatus.DRAFT)
    stale_version = path.record_version
    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=12 * MHZ),
        expected_version=stale_version,
    )

    with pytest.raises(lifecycle.StaleRecordError) as stale:
        lifecycle.edit(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path, input_value=13 * MHZ),
            expected_version=stale_version,
        )

    assert stale.value.current_version == path.record_version


def test_the_refusal_names_the_fields_that_differ(lifecycle_world, make_path):
    """The point of §15.5's diff: an operator can see whether their change still applies."""
    path = make_path(PathStatus.DRAFT)
    stale_version = path.record_version
    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=12 * MHZ),
        expected_version=stale_version,
    )
    path.refresh_from_db()

    with pytest.raises(lifecycle.StaleRecordError) as stale:
        lifecycle.edit(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path, input_value=13 * MHZ, code="LC-RENAMED"),
            expected_version=stale_version,
        )

    changed = {change.field: change for change in stale.value.changes}
    assert changed["input_value"].yours == 13 * MHZ
    assert changed["input_value"].theirs == 12 * MHZ
    assert changed["code"].yours == "LC-RENAMED"
    # Untouched fields stay out of the diff: listing everything would bury the two that matter.
    assert "rolloff" not in changed


def test_a_stale_submission_changes_nothing(lifecycle_world, make_path):
    path = make_path(PathStatus.DRAFT)
    stale_version = path.record_version
    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=12 * MHZ),
        expected_version=stale_version,
    )

    with pytest.raises(lifecycle.StaleRecordError):
        lifecycle.edit(
            actor=lifecycle_world["operator"],
            path=path,
            values=_values(path, input_value=13 * MHZ),
            expected_version=stale_version,
        )

    path.refresh_from_db()
    assert path.occupied_bw_hz == 12 * MHZ


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
def test_the_edit_screen_saves(client, lifecycle_world, make_path):
    path = make_path(PathStatus.PLANNED)
    client.force_login(lifecycle_world["operator"])

    client.post(
        reverse("satnet_paths:edit", kwargs={"pk": path.pk}), _post(path, input_value=25 * MHZ)
    )

    path.refresh_from_db()
    assert path.occupied_bw_hz == 25 * MHZ


def test_a_stale_post_returns_409_with_the_diff(client, lifecycle_world, make_path):
    """409 rather than a form error, matching the wizard: the submission is well-formed and was
    refused by a fact about the world."""
    path = make_path(PathStatus.PLANNED)
    stale = _post(path, input_value=25 * MHZ)
    lifecycle.edit(
        actor=lifecycle_world["operator"],
        path=path,
        values=_values(path, input_value=30 * MHZ),
        expected_version=path.record_version,
    )
    client.force_login(lifecycle_world["operator"])

    response = client.post(reverse("satnet_paths:edit", kwargs={"pk": path.pk}), stale)

    assert response.status_code == 409
    body = response.content.decode()
    assert "changed while you were working" in body
    assert "input_value" in body


def test_the_transition_buttons_carry_the_version(client, lifecycle_world, make_path):
    """§15.5 at the interface. Without this the buttons would be immune to the check the
    service performs, which is most of the point of performing it."""
    path = make_path(PathStatus.PLANNED)
    client.force_login(lifecycle_world["operator"])

    body = client.get(path.get_absolute_url()).content.decode()

    assert f'name="record_version" value="{path.record_version}"' in body


def test_a_transition_posted_with_a_stale_version_returns_409(client, lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)
    stale_version = path.record_version
    lifecycle.transition(actor=lifecycle_world["approver"], path=path, action="suspend")
    client.force_login(lifecycle_world["approver"])

    response = client.post(
        reverse("satnet_paths:transition", kwargs={"pk": path.pk, "action": "retire"}),
        {"record_version": stale_version},
    )

    assert response.status_code == 409
    path.refresh_from_db()
    assert path.status == PathStatus.SUSPENDED


def test_an_illegal_move_over_http_returns_409(client, lifecycle_world, make_path):
    path = make_path(PathStatus.DRAFT)
    client.force_login(lifecycle_world["approver"])

    response = client.post(
        reverse("satnet_paths:transition", kwargs={"pk": path.pk, "action": "retire"})
    )

    assert response.status_code in {403, 409}
    path.refresh_from_db()
    assert path.status == PathStatus.DRAFT


def test_the_detail_screen_offers_only_what_this_reader_may_do(client, lifecycle_world, make_path):
    path = make_path(PathStatus.ON_AIR)
    client.force_login(lifecycle_world["operator"])

    body = client.get(path.get_absolute_url()).content.decode()

    assert "Suspend" not in body
    assert "Retire" not in body


def test_the_wizard_still_creates_a_draft_that_holds_nothing(lifecycle_world):
    """A guard on the S11 behaviour this slice builds on: `create` reserves only when the
    status is operational, and the graph is now what makes it so."""
    from satnet_paths import services

    path = services.create(
        actor=lifecycle_world["operator"],
        satnet=lifecycle_world["satnet"],
        values={
            "code": "LC-DRAFT",
            "direction": "FWD",
            "status": PathStatus.DRAFT,
            "input_mode": InputMode.OCCUPIED_BW,
            "input_value": 10 * MHZ,
            "rolloff": Decimal("0.2"),
            "canonical_center_hz": 50 * MHZ,
            "valid_from": timezone.now(),
        },
    )

    assert not SpectrumReservation.objects.filter(satnet_path_id=path.pk).exists()
