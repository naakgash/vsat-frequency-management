"""Inventory write paths.

Every function follows the shape established in ADR-0013: **authorise, then transact**.
Authorisation inside the transaction would have its denial audit record rolled back along
with the failure.
"""

from __future__ import annotations

from typing import Any, cast

from django.db import models, transaction

from accounts import policy
from accounts.models import User
from accounts.types import Actor
from audit import services as audit_services
from inventory import dependencies, versioning
from inventory.constants import (
    INVENTORY_CREATED,
    INVENTORY_DEACTIVATED,
    INVENTORY_REACTIVATED,
    INVENTORY_UPDATED,
    MANAGE_INVENTORY,
)
from inventory.models import InventoryRecord, MasterDataVersioned


class StaleRecordError(Exception):
    """Raised when a form was rendered from a version that has since changed."""


class InUseError(Exception):
    """Raised when deactivating an object that other records still depend on."""

    def __init__(self, instance: models.Model, blocking: list[Any]) -> None:
        self.instance = instance
        self.blocking = blocking
        summary = ", ".join(f"{d.count} {d.label}" for d in blocking)
        super().__init__(
            f"{instance} is still in use by {summary}. Deactivate or reassign those first."
        )


def create[ModelT: InventoryRecord](
    *, actor: Actor, model: type[ModelT], values: dict[str, Any], reason: str = ""
) -> ModelT:
    """Create an inventory record."""
    policy.require(actor, MANAGE_INVENTORY, reason=reason)
    return _create(actor=actor, model=model, values=values, reason=reason)


@transaction.atomic
def _create[ModelT: InventoryRecord](
    *, actor: Actor, model: type[ModelT], values: dict[str, Any], reason: str
) -> ModelT:
    instance = model(**values)
    if hasattr(instance, "created_by"):
        instance.created_by = _acting_user(actor)
        instance.updated_by = instance.created_by
    instance.full_clean(exclude=_excluded_from_clean(instance))
    instance.save()

    audit_services.record(
        action=INVENTORY_CREATED,
        actor=actor,
        obj=instance,
        after=audit_services.snapshot(instance),
        change_reason=reason,
        message=f"Created {instance._meta.verbose_name} {instance}",
    )
    return instance


def update[ModelT: InventoryRecord](
    *,
    actor: Actor,
    instance: ModelT,
    values: dict[str, Any],
    expected_version: int | None = None,
    reason: str = "",
) -> ModelT:
    """Update an inventory record, with optimistic locking (section 15.5).

    A versioned record whose engineering values are referenced by operational data cannot
    be changed in place: section 13.6 requires a new version instead. The guard lives here
    rather than in the form so the importer and any management command inherit it.
    """
    policy.require(actor, MANAGE_INVENTORY, instance, reason=reason)

    if isinstance(instance, MasterDataVersioned):
        changed = {
            field for field, value in values.items() if getattr(instance, field, None) != value
        }
        versioning.assert_editable(instance, changed)

    return _update(
        actor=actor,
        instance=instance,
        values=values,
        expected_version=expected_version,
        reason=reason,
    )


@transaction.atomic
def _update[ModelT: InventoryRecord](
    *,
    actor: Actor,
    instance: ModelT,
    values: dict[str, Any],
    expected_version: int | None,
    reason: str,
) -> ModelT:
    before = audit_services.snapshot(instance)
    current_version = getattr(instance, "record_version", None)

    for field, value in values.items():
        setattr(instance, field, value)
    if hasattr(instance, "updated_by"):
        instance.updated_by = _acting_user(actor)

    instance.full_clean(exclude=_excluded_from_clean(instance))

    if current_version is not None:
        target = expected_version if expected_version is not None else current_version
        instance.record_version = target + 1
        # _default_manager rather than .objects: an abstract base has no manager of its
        # own, and this is the documented way to reach a concrete model's manager from
        # generic code.
        manager = cast(Any, type(instance))._default_manager
        updated = manager.filter(pk=instance.pk, record_version=target).update(
            **values,
            record_version=instance.record_version,
            updated_by=instance.updated_by,
        )
        if updated == 0:
            raise StaleRecordError(
                "This record was changed by someone else while you were editing it. "
                "Reload the page to see the current values."
            )
        instance.refresh_from_db()
    else:
        instance.save()

    after = audit_services.snapshot(instance)
    audit_services.record(
        action=INVENTORY_UPDATED,
        actor=actor,
        obj=instance,
        before=before,
        after=after,
        change_reason=reason,
        message=f"Updated {instance._meta.verbose_name} {instance}",
    )
    return instance


def set_active[ModelT: InventoryRecord](
    *, actor: Actor, instance: ModelT, active: bool, reason: str = ""
) -> ModelT:
    """Activate or deactivate a record.

    Deactivation is refused while other records depend on it — specification section 3:
    *"prevent invalid deletion or deactivation when an object is in use"*. There is no
    delete path at all: section 20 forbids hard-deleting used inventory, so deactivation
    is the only retirement mechanism.
    """
    policy.require(actor, MANAGE_INVENTORY, instance, reason=reason)

    if not active:
        blocking = dependencies.blocking_dependencies(instance)
        if blocking:
            raise InUseError(instance, blocking)

    return _set_active(actor=actor, instance=instance, active=active, reason=reason)


@transaction.atomic
def _set_active[ModelT: InventoryRecord](
    *, actor: Actor, instance: ModelT, active: bool, reason: str
) -> ModelT:
    before = {"is_active": instance.is_active}
    instance.is_active = active
    if hasattr(instance, "updated_by"):
        instance.updated_by = _acting_user(actor)
    instance.save(update_fields=["is_active", "updated_by", "updated_at"])

    audit_services.record(
        action=INVENTORY_REACTIVATED if active else INVENTORY_DEACTIVATED,
        actor=actor,
        obj=instance,
        before=before,
        after={"is_active": active},
        change_reason=reason,
        message=f"{'Activated' if active else 'Deactivated'} {instance}",
    )
    return instance


def _acting_user(actor: Actor) -> User | None:
    """Narrow an actor to a persisted user.

    An ``AnonymousUser`` cannot be stored in a ``created_by`` column, and never reaches
    here in practice because authorisation runs first — but the audit columns are nullable
    precisely so that a system-initiated write has somewhere to go.
    """
    return actor if isinstance(actor, User) else None


def _excluded_from_clean(instance: models.Model) -> list[str]:
    """Fields ``full_clean`` should not validate.

    Unique checks are left to the database: doing them in Python races, and the database
    constraint is the authority either way (specification section 8.3 applied generally).
    """
    return [f.name for f in instance._meta.fields if f.name in {"created_by", "updated_by"}]
