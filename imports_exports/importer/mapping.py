"""Spreadsheet labels to records, and remembering the answers. §17.1.

Two ways a label resolves, tried in this order:

1. **The platform's own code.** A cell reading ``SN-KA-01`` finds the Satnet whose code is
   ``SN-KA-01``. Exact, case-insensitive, and nothing else — no prefix matching, no closest
   match, no ignoring punctuation. A near-match that resolved on its own would attach somebody's
   transmission to the wrong Satnet, which is the single mistake an import must not be able to
   make unsupervised.
2. **A remembered mapping.** An administrator who has once said that ``Ka Hub 1 (old)`` means a
   particular Satnet is not asked again. That is the whole reason `import_mapping` exists: a
   reviewer asked the same forty questions on every monthly import stops reading them.

Anything else is :data:`NEEDS_MAPPING` — a row the importer refuses to guess at, reported with
the label it could not place.

**A Satnet resolves within its Beam, not globally.** Satnet codes are unique per Beam
(**A-18**), so a bare code can legitimately name two records. Where it does, the row is reported
as ambiguous rather than resolved to whichever came first — the same reasoning as the near-match
above, and the remembered mapping is how it gets settled.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from accounts.types import Actor
from audit import services as audit_services
from imports_exports.constants import IMPORT_MAPPING_REMEMBERED
from imports_exports.importer.fields import GATEWAY, SATNET
from imports_exports.models import ImportMapping

#: Where a label is looked up when it is not a remembered mapping. One entry per reference kind
#: an import may carry; adding a kind means adding a row here and a `Field` in `fields`.
_REGISTRIES: dict[str, str] = {
    SATNET: "satnets.Satnet",
    GATEWAY: "inventory.Gateway",
}


@dataclasses.dataclass(frozen=True)
class Resolution:
    """What a label turned out to mean, or why it did not."""

    kind: str
    label: str
    target: Any = None
    #: ``code``, ``remembered``, ``unknown`` or ``ambiguous`` — how it was decided, so the
    #: review screen can say "this came from a mapping you made" rather than presenting every
    #: resolution as equally direct.
    source: str = "unknown"

    @property
    def ok(self) -> bool:
        return self.target is not None

    @property
    def reason(self) -> str:
        if self.ok:
            return ""
        if self.source == "ambiguous":
            return (
                f"{self.label!r} names more than one {self.kind}. Codes are unique per Beam "
                f"(A-18), so this one needs a mapping to say which record is meant."
            )
        return (
            f"{self.label!r} does not match any {self.kind} this platform holds. Add a mapping "
            f"for it, or correct the spreadsheet."
        )


def resolve(kind: str, label: str) -> Resolution:
    """Turn one label into a record, or say why not."""
    text = (label or "").strip()
    if not text:
        return Resolution(kind=kind, label=text, source="unknown")

    remembered = ImportMapping.objects.filter(kind=kind, label=text).first()
    if remembered is not None:
        target = _model(kind).objects.filter(pk=remembered.target_id).first()
        if target is not None:
            return Resolution(kind=kind, label=text, target=target, source="remembered")

    matches = list(_model(kind).objects.filter(code__iexact=text)[:2])
    if len(matches) == 1:
        return Resolution(kind=kind, label=text, target=matches[0], source="code")
    if len(matches) > 1:
        return Resolution(kind=kind, label=text, source="ambiguous")
    return Resolution(kind=kind, label=text, source="unknown")


def remember(*, actor: Actor, kind: str, label: str, target_id: uuid.UUID) -> ImportMapping:
    """Record what a label means, so the next import does not ask. §17.1, §18.

    Audited, because it is a decision rather than a preference: every future import of that
    label silently follows it, and "who decided that ``Ka Hub 1 (old)`` was this Satnet" is a
    question somebody will eventually ask.
    """
    if kind not in _REGISTRIES:
        raise ValueError(f"{kind!r} is not a reference an import resolves.")

    target = _model(kind).objects.get(pk=target_id)
    record, created = ImportMapping.objects.update_or_create(
        kind=kind,
        label=label.strip(),
        defaults={
            "target_id": target_id,
            "target_repr": str(target)[:255],
            "created_by": _acting_user(actor),
        },
    )
    audit_services.record(
        action=IMPORT_MAPPING_REMEMBERED,
        actor=actor,
        obj=record,
        after={"kind": kind, "label": record.label, "target": record.target_repr},
        message=(
            f"{'Recorded' if created else 'Changed'} the import mapping "
            f"{record.label!r} → {record.target_repr}"
        ),
    )
    return record


def known(kind: str) -> list[ImportMapping]:
    return list(ImportMapping.objects.filter(kind=kind))


def candidates(kind: str, limit: int = 200) -> list[Any]:
    """What an administrator can choose from when supplying a mapping."""
    return list(_model(kind).objects.all()[:limit])


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _model(kind: str) -> Any:
    from django.apps import apps

    try:
        label = _REGISTRIES[kind]
    except KeyError as exc:
        raise ValueError(f"{kind!r} is not a reference an import resolves.") from exc
    return apps.get_model(label)


def _acting_user(actor: Actor) -> Any:
    from accounts.models import User

    return actor if isinstance(actor, User) else None
