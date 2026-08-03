"""What depends on an inventory object.

Specification section 3.2 requires dependency summaries on detail screens — *"Used by 4
Beams"*, *"Contains 6 active Satnets"* — and section 3 requires the interface to *"prevent
invalid deletion or deactivation when an object is in use"*.

The objects that will do the depending (Frequency Window, Payload Path, Beam, Satnet)
arrive in later slices and sit **above** inventory in the dependency direction, so
inventory cannot import them. Instead each module registers its own dependency as it
lands::

    # beams/apps.py, in ready()
    from inventory import dependencies
    dependencies.register(
        Satellite,
        label="Beams",
        count=lambda satellite: Beam.objects.filter(satellite=satellite).count(),
        blocks_deactivation=True,
    )

Same pattern as the scope registry in ``accounts/scope.py``, and for the same reason: it
keeps a lower module free of imports from a higher one while still letting the higher one
contribute behaviour.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from django.db import models


@dataclasses.dataclass(frozen=True)
class Dependency:
    """One thing that may depend on an inventory object."""

    label: str
    count: Callable[[Any], int]
    #: When true, a non-zero count refuses deactivation. When false the count is shown
    #: for information only — a historical reference does not block a change.
    blocks_deactivation: bool = True
    #: Optional URL for a "view these" link on the detail screen.
    url: Callable[[Any], str] | None = None


@dataclasses.dataclass(frozen=True)
class DependencyCount:
    """A resolved dependency, ready to render."""

    label: str
    count: int
    blocks_deactivation: bool
    url: str = ""

    @property
    def summary(self) -> str:
        """The wording of specification section 3.2, e.g. "Used by 4 Beams"."""
        return f"{self.count} {self.label}"


_REGISTRY: dict[type[models.Model], list[Dependency]] = {}


def register(
    model: type[models.Model],
    *,
    label: str,
    count: Callable[[Any], int],
    blocks_deactivation: bool = True,
    url: Callable[[Any], str] | None = None,
) -> None:
    """Declare that something depends on ``model``.

    Registering the same label twice replaces the earlier entry, so a repeated
    ``AppConfig.ready()`` under autoreload does not duplicate a row in the summary.
    """
    dependency = Dependency(
        label=label, count=count, blocks_deactivation=blocks_deactivation, url=url
    )
    existing = [d for d in _REGISTRY.get(model, []) if d.label != label]
    _REGISTRY[model] = [*existing, dependency]


def clear(model: type[models.Model] | None = None) -> None:
    """Remove registrations. Used by tests to restore a clean registry."""
    if model is None:
        _REGISTRY.clear()
    else:
        _REGISTRY.pop(model, None)


def registered_for(model: type[models.Model]) -> list[Dependency]:
    return list(_REGISTRY.get(model, []))


def summarise(instance: models.Model) -> list[DependencyCount]:
    """Resolve every dependency of ``instance``, including zero counts.

    Zeros are included deliberately: "0 Beams" tells an administrator the object is safe
    to deactivate, whereas an omitted row is ambiguous between "none" and "not checked".
    """
    results = []
    for dependency in _REGISTRY.get(type(instance), []):
        results.append(
            DependencyCount(
                label=dependency.label,
                count=dependency.count(instance),
                blocks_deactivation=dependency.blocks_deactivation,
                url=dependency.url(instance) if dependency.url else "",
            )
        )
    return results


def blocking_dependencies(instance: models.Model) -> list[DependencyCount]:
    """Dependencies that currently prevent deactivation."""
    return [d for d in summarise(instance) if d.blocks_deactivation and d.count > 0]


def is_in_use(instance: models.Model) -> bool:
    return bool(blocking_dependencies(instance))
