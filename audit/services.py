"""Audit write path.

Every module records through :func:`record`. Writing ``AuditEvent.objects.create()``
directly elsewhere is a defect: it bypasses the request-context capture and the
serialisation rules that keep ``before``/``after`` diffable.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.db import models

from audit import context
from audit.constants import AuditOutcome
from audit.models import AuditEvent

# Field names whose values must never reach the audit trail, even in a before/after
# diff. Matched case-insensitively against a substring of the field name.
SENSITIVE_FIELD_MARKERS = ("password", "secret", "token", "api_key", "salt")

REDACTED = "[redacted]"


def record(
    *,
    action: str,
    actor: Any = None,
    outcome: str = AuditOutcome.SUCCESS,
    obj: models.Model | None = None,
    object_type: str = "",
    object_id: uuid.UUID | None = None,
    object_repr: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    change_reason: str = "",
    message: str = "",
    import_batch_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Write one audit event.

    ``actor`` is typed loosely because an anonymous user is not a ``User`` instance;
    passing ``request.user`` directly must work whether or not anyone is signed in.
    """
    request = context.current()

    actor_user = actor if _is_persisted_user(actor) else None
    actor_username = ""
    if actor is not None:
        actor_username = str(getattr(actor, "get_username", lambda: "")() or "")

    if obj is not None:
        object_type = object_type or f"{obj._meta.app_label}.{obj._meta.object_name}"
        object_id = object_id or _uuid_or_none(obj.pk)
        object_repr = object_repr or str(obj)[:255]

    return AuditEvent.objects.create(
        action=action,
        outcome=outcome,
        actor=actor_user,
        actor_username=actor_username[:150],
        object_type=object_type[:100],
        object_id=object_id,
        object_repr=object_repr[:255],
        before=_sanitize(before),
        after=_sanitize(after),
        change_reason=change_reason,
        message=message,
        request_id=request.request_id,
        source_ip=request.source_ip,
        user_agent=request.user_agent[:512],
        import_batch_id=import_batch_id,
    )


def snapshot(instance: models.Model, fields: list[str] | None = None) -> dict[str, Any]:
    """Capture a model's concrete field values for a before/after diff.

    Only concrete fields are captured: relations are recorded as their primary key, so a
    diff never triggers a cascade of queries and never embeds a related object's whole
    state.
    """
    concrete = {f.name: f for f in instance._meta.concrete_fields}
    names = fields if fields is not None else list(concrete)

    captured: dict[str, Any] = {}
    for name in names:
        field = concrete.get(name)
        if field is None:
            # Reverse relations and generic foreign keys are not concrete columns, so
            # they have no stored value to diff. Skipping them keeps a snapshot from
            # silently triggering extra queries or embedding a related object's state.
            continue
        # For a relation, attname is the raw *_id column: the primary key, not the
        # related object, which is what belongs in an immutable diff.
        value = getattr(instance, field.attname if field.is_relation else field.name, None)
        captured[name] = _jsonable(value)
    return _sanitize(captured) or {}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only the fields that changed, as ``{field: {"before": x, "after": y}}``."""
    changed = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}
    return changed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _is_persisted_user(actor: Any) -> bool:
    """True only for a saved user instance.

    ``AnonymousUser`` has no primary key, and an unsaved instance would raise on assignment.
    """
    return actor is not None and getattr(actor, "pk", None) is not None


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _sanitize(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact sensitive values before they are persisted.

    A password hash in an audit diff is a credential in a table that, by design, can
    never be corrected or deleted. Redaction has to happen on the way in.
    """
    if payload is None:
        return None
    return {
        key: (REDACTED if _is_sensitive(key) else _jsonable(value))
        for key, value in payload.items()
    }


def _is_sensitive(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)


def _jsonable(value: Any) -> Any:
    """Convert a field value to something JSON can hold without losing precision.

    Decimal becomes a string rather than a float: roll-off is an exact decimal
    (specification section 14.1), and a float round-trip would corrupt the very values
    the audit trail exists to preserve.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)
