"""Specification Dictionary: registry consistency, permissions and immutability.

Specification section 2, acceptance criteria 26.2 and 26.3.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from audit.models import AuditEvent
from specifications import selectors, services
from specifications.constants import SPECIFICATION_UPDATED
from specifications.models import SpecificationDefinition
from specifications.registry import SPECIFICATION_SEEDS, SYSTEM_CODES
from tests.factories import TEST_PASSWORD, make_admin, make_observer, make_operator

DICTIONARY_URL = "/specifications/"


def _sign_in(client, user) -> None:
    assert client.login(username=user.get_username(), password=TEST_PASSWORD)


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_every_registered_code_has_a_dictionary_row():
    """A code the application refers to but the dictionary lacks renders as a bare code
    with no explanation — the exact failure section 2 exists to prevent."""
    stored = set(SpecificationDefinition.objects.values_list("code", flat=True))

    assert SYSTEM_CODES <= stored, f"missing rows for: {sorted(SYSTEM_CODES - stored)}"


@pytest.mark.django_db
def test_every_seeded_row_is_marked_system_managed():
    for seed in SPECIFICATION_SEEDS:
        definition = SpecificationDefinition.objects.get(code=seed.code)
        assert definition.is_system_managed, f"{seed.code} should be system-managed"


@pytest.mark.django_db
def test_the_codes_named_in_the_specification_are_all_present():
    """The eleven codes listed verbatim in specification section 2."""
    named_in_spec = {
        "FWD_HUB_UL_START_RF",
        "FWD_HUB_UL_CENTER_RF",
        "FWD_HUB_UL_END_RF",
        "FWD_REMOTE_DL_CENTER_RF",
        "RTN_REMOTE_UL_CENTER_RF",
        "RTN_HUB_DL_CENTER_RF",
        "L_BAND_CENTER_IF",
        "SYMBOL_RATE",
        "ROLLOFF",
        "OCCUPIED_BANDWIDTH",
        "ALLOCATED_BANDWIDTH",
    }
    stored = set(SpecificationDefinition.objects.values_list("code", flat=True))

    assert named_in_spec <= stored, f"missing: {sorted(named_in_spec - stored)}"


@pytest.mark.django_db
def test_no_rf_engineering_value_was_invented():
    """Specification section 26.20 and the header of specifications/registry.py.

    Where a formula depends on an unconfirmed engineering rule — the payload translation
    (OQ-02) and the equipment conversion (OQ-04) — the calculation note must be empty
    rather than filled with a plausible guess.
    """
    unconfirmed = [
        "FWD_REMOTE_DL_CENTER_RF",  # translation method and constant: OQ-02
        "RTN_HUB_DL_CENTER_RF",  # OQ-02
        "L_BAND_CENTER_IF",  # LO and sideband: OQ-04
    ]
    for code in unconfirmed:
        definition = SpecificationDefinition.objects.get(code=code)
        assert definition.calculation_note == "", (
            f"{code} has a calculation note, but the rule behind it is an unresolved "
            f"OPEN QUESTION. Do not invent it."
        )


# ---------------------------------------------------------------------------
# Permissions (specification section 25)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_admin, make_operator, make_observer])
def test_every_role_may_read_the_dictionary(client, factory):
    """An Operator needs to look up what a code means."""
    _sign_in(client, factory())

    assert client.get(DICTIONARY_URL).status_code == 200


@pytest.mark.django_db
def test_anonymous_cannot_read_the_dictionary(client):
    response = client.get(DICTIONARY_URL)

    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
def test_admin_can_edit_specification_metadata(client):
    """Acceptance criterion 26.2."""
    admin = make_admin()
    _sign_in(client, admin)
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")

    response = client.post(
        f"/specifications/{definition.code}/edit/",
        _edit_payload(definition, display_name="Transmission symbol rate"),
    )

    assert response.status_code == 302
    definition.refresh_from_db()
    assert definition.display_name == "Transmission symbol rate"


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [make_operator, make_observer])
def test_non_admins_cannot_edit_by_direct_post(client, factory):
    """Section 25: only an admin edits specification metadata. Posted directly, because
    hiding the Edit button is not the control."""
    _sign_in(client, factory())
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")
    original = definition.display_name

    response = client.post(
        f"/specifications/{definition.code}/edit/",
        _edit_payload(definition, display_name="Tampered"),
    )

    assert response.status_code == 403
    definition.refresh_from_db()
    assert definition.display_name == original


# ---------------------------------------------------------------------------
# Code immutability (assumption A-20)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_edit_form_ignores_a_posted_code(client):
    """The form omits ``code`` entirely, so a crafted POST cannot rename one."""
    _sign_in(client, make_admin())
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")

    payload = _edit_payload(definition)
    payload["code"] = "RENAMED_BY_FORM"
    client.post(f"/specifications/{definition.code}/edit/", payload)

    definition.refresh_from_db()
    assert definition.code == "SYMBOL_RATE"


@pytest.mark.django_db
def test_the_service_rejects_a_code_change():
    admin = make_admin()
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")

    with pytest.raises(ValueError, match="not editable"):
        services.update_specification(
            actor=admin, specification=definition, changes={"code": "RENAMED"}
        )


@pytest.mark.django_db
def test_the_database_rejects_renaming_a_system_managed_code():
    """The layer below the form and the service.

    A data migration or a psql session would otherwise be able to rename a code and
    silently detach it from the calculation engine that refers to it by name.
    """
    with pytest.raises(IntegrityError, match="cannot be renamed"), transaction.atomic():
        SpecificationDefinition.objects.filter(code="SYMBOL_RATE").update(code="RENAMED")


@pytest.mark.django_db
def test_a_non_system_code_may_be_renamed():
    """The restriction applies only to codes application logic depends on."""
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")
    custom = SpecificationDefinition.objects.create(
        code="LOCAL_NOTE",
        is_system_managed=False,
        display_name="Local note",
        category=definition.category,
        data_type="TEXT",
    )

    SpecificationDefinition.objects.filter(pk=custom.pk).update(code="LOCAL_NOTE_2")

    custom.refresh_from_db()
    assert custom.code == "LOCAL_NOTE_2"


# ---------------------------------------------------------------------------
# Audit and optimistic locking
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_edit_is_audited_with_before_and_after():
    admin = make_admin()
    definition = SpecificationDefinition.objects.get(code="ROLLOFF")

    services.update_specification(
        actor=admin,
        specification=definition,
        changes={**_current_values(definition), "display_name": "Roll-off"},
        expected_version=definition.record_version,
        reason="Shorter label for dense tables",
    )

    event = AuditEvent.objects.get(action=SPECIFICATION_UPDATED)
    assert event.actor_id == admin.pk
    assert event.before["display_name"] == "Roll-off factor"
    assert event.after["display_name"] == "Roll-off"
    assert event.change_reason == "Shorter label for dense tables"


@pytest.mark.django_db
def test_a_stale_edit_is_rejected():
    """Specification section 15.5: a stale form submission must be rejected."""
    admin = make_admin()
    definition = SpecificationDefinition.objects.get(code="ROLLOFF")
    stale_version = definition.record_version

    services.update_specification(
        actor=admin,
        specification=definition,
        changes={**_current_values(definition), "display_name": "First edit"},
        expected_version=stale_version,
    )

    definition.refresh_from_db()
    with pytest.raises(services.StaleRecordError):
        services.update_specification(
            actor=admin,
            specification=definition,
            changes={**_current_values(definition), "display_name": "Second edit"},
            expected_version=stale_version,
        )


@pytest.mark.django_db
def test_a_denied_edit_is_audited():
    observer = make_observer()
    definition = SpecificationDefinition.objects.get(code="SYMBOL_RATE")

    with pytest.raises(PermissionDenied):
        services.update_specification(
            actor=observer, specification=definition, changes={"display_name": "Nope"}
        )

    assert AuditEvent.objects.filter(action="PERMISSION_DENIED", actor=observer).exists()


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_missing_code_returns_none_rather_than_raising():
    """A missing entry must degrade to showing the bare code, not break the page."""
    assert selectors.get_definition("NO_SUCH_CODE") is None


@pytest.mark.django_db
def test_priming_the_cache_avoids_a_query_per_code(rf, django_assert_num_queries):
    request = rf.get("/")
    codes = [seed.code for seed in SPECIFICATION_SEEDS]

    with django_assert_num_queries(1):
        selectors.prime_cache(codes, request)
        for code in codes:
            selectors.get_definition(code, request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_values(definition: SpecificationDefinition) -> dict:
    return {field: getattr(definition, field) for field in services.EDITABLE_FIELDS}


def _edit_payload(definition: SpecificationDefinition, **overrides) -> dict:
    payload = {
        field: getattr(definition, field)
        for field in services.EDITABLE_FIELDS
        if not isinstance(getattr(definition, field), bool)
    }
    # Unchecked checkboxes are simply absent from a real form submission.
    payload.update(
        {field: "on" for field in services.EDITABLE_FIELDS if getattr(definition, field) is True}
    )
    payload["expected_version"] = definition.record_version
    payload["reason"] = "test"
    payload.update(overrides)
    return payload
