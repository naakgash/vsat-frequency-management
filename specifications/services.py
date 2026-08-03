"""Specification Dictionary write paths."""

from __future__ import annotations

from typing import Any

from django.db import transaction

from accounts import policy
from accounts.types import Actor
from audit import constants as audit_constants
from audit import services as audit_services
from specifications.constants import CHANGE_SPECIFICATION, SPECIFICATION_UPDATED
from specifications.models import SpecificationDefinition

#: Fields an administrator may edit. ``code`` is absent by design: it is the stable
#: identifier application logic refers to (specification section 2, assumption A-20).
EDITABLE_FIELDS = (
    "display_name",
    "short_name",
    "description",
    "help_text",
    "unit",
    "display_precision",
    "calculation_note",
    "source_reference",
    "visible_in_tables",
    "visible_in_forms",
    "visible_in_detail",
    "display_order",
    "is_active",
)


class StaleRecordError(Exception):
    """Raised when a form was rendered from a version that has since changed."""


def update_specification(
    *,
    actor: Actor,
    specification: SpecificationDefinition,
    changes: dict[str, Any],
    expected_version: int | None = None,
    reason: str = "",
) -> SpecificationDefinition:
    """Update a specification's presentation metadata.

    Authorisation runs before the transaction opens, for the reason set out in
    ADR-0013: a denial recorded inside the unit of work is rolled back with it.
    """
    policy.require(actor, CHANGE_SPECIFICATION, specification, reason=reason)
    return _apply_update(
        actor=actor,
        specification=specification,
        changes=changes,
        expected_version=expected_version,
        reason=reason,
    )


@transaction.atomic
def _apply_update(
    *,
    actor: Actor,
    specification: SpecificationDefinition,
    changes: dict[str, Any],
    expected_version: int | None,
    reason: str,
) -> SpecificationDefinition:
    rejected = sorted(set(changes) - set(EDITABLE_FIELDS))
    if rejected:
        # "code" landing here is the case this guards: renaming a code would silently
        # detach it from the calculation engine that refers to it by name.
        raise ValueError(
            f"These fields are not editable through the dictionary: {', '.join(rejected)}"
        )

    before = audit_services.snapshot(specification, list(EDITABLE_FIELDS))

    for field, value in changes.items():
        setattr(specification, field, value)
    specification.record_version += 1

    # Optimistic locking (specification section 15.5): a conditional update, so a
    # concurrent edit loses rather than silently overwriting.
    updated = SpecificationDefinition.objects.filter(
        pk=specification.pk,
        record_version=(
            expected_version if expected_version is not None else specification.record_version - 1
        ),
    ).update(**changes, record_version=specification.record_version)

    if updated == 0:
        raise StaleRecordError(
            "This specification was changed by someone else while you were editing it. "
            "Reload the page to see the current values."
        )

    specification.refresh_from_db()
    after = audit_services.snapshot(specification, list(EDITABLE_FIELDS))

    audit_services.record(
        action=SPECIFICATION_UPDATED,
        actor=actor,
        obj=specification,
        before=before,
        after=after,
        change_reason=reason,
        message=f"Updated specification {specification.code}",
    )
    return specification


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convenience re-export so callers do not reach into the audit module."""
    return audit_services.diff(before, after)


__all__ = [
    "EDITABLE_FIELDS",
    "StaleRecordError",
    "audit_constants",
    "changed_fields",
    "update_specification",
]
