"""Master-data versioning, shared by the three engineering-critical entities.

Specification section 13.6: *"A Window in operational use is changed through versioning,
not retroactive overwrite."* Design assumption **A-16** extends that to Payload Paths and
Equipment Profiles, and this is the one implementation all three use.

The rule it enforces is narrow and important. An operational record — a Satnet Path, in
S11 — references a *specific version row*. If someone could edit that row's frequencies,
every allocation validated against the old numbers would silently be describing something
else. So an in-use version is immutable, and a change means a successor.

What "in use" means grows with the product. Today only a Payload Path can reference a
Frequency Window; from S8 a Beam can, and from S11 a Satnet Path can. Rather than teach
this module about modules above it, usage is reported through the same registry the
dependency summaries use (``inventory.dependencies``), which those modules populate
themselves.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts import policy
from accounts.types import Actor
from audit import services as audit_services
from inventory import dependencies
from inventory.constants import MANAGE_INVENTORY, MASTER_DATA_VERSIONED
from inventory.models import MasterDataVersioned


class RetroactiveEditError(Exception):
    """Raised when editing a version that operational records already reference."""

    def __init__(self, instance: Any, blocking: list[Any]) -> None:
        self.instance = instance
        self.blocking = blocking
        summary = ", ".join(f"{d.count} {d.label}" for d in blocking)
        super().__init__(
            f"{instance} is referenced by {summary}. Engineering values on a record in "
            f"operational use cannot be changed retroactively — create a new version "
            f"instead, so existing allocations keep the definition they were validated "
            f"against."
        )


#: Fields whose change alters what an allocation was validated against. Editing one of
#: these on an in-use version is refused; anything else — a name, a description, a
#: reference document — may be corrected in place.
ENGINEERING_FIELDS: dict[str, frozenset[str]] = {
    "FrequencyWindow": frozenset(
        {
            "rf_start_hz",
            "rf_end_hz",
            "side",
            "polarization",
            "min_edge_guard_hz",
            "satellite",
            "band",
        }
    ),
    "PayloadPath": frozenset(
        {
            "direction",
            "uplink_window",
            "downlink_window",
            "translation_method",
            "translation_constant_hz",
            "spectral_inversion",
            "satellite",
        }
    ),
    "EquipmentProfile": frozenset(
        {
            "rf_min_hz",
            "rf_max_hz",
            "if_min_hz",
            "if_max_hz",
            "lo_hz",
            "conversion_method",
            "sideband",
            "spectral_inversion",
            "band",
        }
    ),
}


def engineering_fields_for(instance: MasterDataVersioned) -> frozenset[str]:
    return ENGINEERING_FIELDS.get(type(instance).__name__, frozenset())


def is_in_operational_use(instance: MasterDataVersioned) -> list[Any]:
    """Dependencies that make this version's engineering values immutable."""
    return dependencies.blocking_dependencies(instance)


def assert_editable(instance: MasterDataVersioned, changed_fields: set[str]) -> None:
    """Refuse an engineering change to a version that is in operational use.

    Non-engineering corrections are always allowed: refusing to fix a typo in a
    description would push people towards editing the database directly, which is worse
    than the thing the rule protects against.
    """
    engineering_changes = changed_fields & engineering_fields_for(instance)
    if not engineering_changes:
        return

    blocking = is_in_operational_use(instance)
    if blocking:
        raise RetroactiveEditError(instance, blocking)


def supersede[VersionedT: MasterDataVersioned](
    *,
    actor: Actor,
    instance: VersionedT,
    values: dict[str, Any],
    effective_from: Any = None,
    reason: str = "",
) -> VersionedT:
    """Create the next version of a record, closing the current one.

    Ordering inside the transaction is load-bearing. The predecessor's period is closed
    **before** the successor is inserted, because ``excl_*_version_overlap`` is checked
    per statement: inserting first would momentarily leave two active versions of the same
    group and the constraint would reject it. This is the same close-then-open discipline
    the reservation engine will need in S12 (**A-14**).
    """
    policy.require(actor, MANAGE_INVENTORY, instance, reason=reason)
    return _supersede(
        actor=actor,
        instance=instance,
        values=values,
        effective_from=effective_from or timezone.now(),
        reason=reason,
    )


@transaction.atomic
def _supersede[VersionedT: MasterDataVersioned](
    *,
    actor: Actor,
    instance: VersionedT,
    values: dict[str, Any],
    effective_from: Any,
    reason: str,
) -> VersionedT:
    if instance.effective_until is not None:
        raise ValueError(
            f"{instance} has already been superseded; create the next version from the "
            f"current one instead."
        )
    if effective_from <= instance.effective_from:
        raise ValueError(
            "The new version must take effect after the one it replaces, or the two would overlap."
        )

    before = audit_services.snapshot(instance)

    # 1. Close the predecessor. Must happen first — see the docstring.
    type(instance)._default_manager.filter(pk=instance.pk).update(effective_until=effective_from)
    instance.refresh_from_db()

    # 2. Open the successor, carrying everything not explicitly changed.
    successor = _copy_for_next_version(instance, values, effective_from)
    successor.full_clean(exclude=["created_by", "updated_by", "effective_period"])
    successor.save()

    after = audit_services.snapshot(successor)
    audit_services.record(
        action=MASTER_DATA_VERSIONED,
        actor=actor,
        obj=successor,
        before=before,
        after=after,
        change_reason=reason,
        message=(
            f"Superseded {type(instance).__name__} {instance} "
            f"(v{instance.version_number} -> v{successor.version_number})"
        ),
    )
    return successor


def _copy_for_next_version[VersionedT: MasterDataVersioned](
    instance: VersionedT, values: dict[str, Any], effective_from: Any
) -> VersionedT:
    """Build the successor row from its predecessor plus the requested changes."""
    model = type(instance)
    skip = {"id", "effective_period", "created_at", "updated_at", "created_by", "updated_by"}

    fields: dict[str, Any] = {}
    for field in model._meta.concrete_fields:
        if field.name in skip or field.name == "supersedes":
            continue
        fields[field.attname if field.is_relation else field.name] = getattr(
            instance, field.attname if field.is_relation else field.name
        )

    fields.update(values)
    fields["id"] = uuid.uuid4()
    fields["version_group"] = instance.version_group
    fields["version_number"] = instance.version_number + 1
    fields["supersedes"] = instance
    fields["effective_from"] = effective_from
    fields["effective_until"] = None
    fields["record_version"] = 1

    return model(**fields)


def version_history(instance: MasterDataVersioned) -> list[MasterDataVersioned]:
    """Every version of this record's group, oldest first."""
    return list(
        type(instance)
        ._default_manager.filter(version_group=instance.version_group)
        .order_by("version_number")
    )


def current_version(instance: MasterDataVersioned) -> MasterDataVersioned | None:
    """The version currently in force for this group, if any."""
    return (
        type(instance)
        ._default_manager.filter(version_group=instance.version_group, is_active=True)
        .filter(effective_until__isnull=True)
        .order_by("-version_number")
        .first()
    )
