"""Inventory models.

Split into modules because the specification splits the entities: section 3.1 lists the
independent objects, section 3.2 the dependent ones. ``base`` holds the behaviour they
share.

Everything is re-exported here, so ``from inventory.models import Gateway`` continues to
work regardless of which module a model lives in.
"""

from inventory.models.base import (
    DeactivatableModel,
    EffectiveDatedModel,
    InventoryRecord,
    MasterDataVersioned,
    TimestampedModel,
)
from inventory.models.dependent import (
    FrequencyWindow,
    GuardPolicy,
    PayloadPath,
    PayloadPolarizationMapping,
)
from inventory.models.independent import (
    Band,
    BandPolarization,
    EquipmentProfile,
    Gateway,
    Hub,
    Satellite,
)

__all__ = [
    "Band",
    "BandPolarization",
    "DeactivatableModel",
    "EffectiveDatedModel",
    "EquipmentProfile",
    "FrequencyWindow",
    "Gateway",
    "GuardPolicy",
    "Hub",
    "InventoryRecord",
    "MasterDataVersioned",
    "PayloadPath",
    "PayloadPolarizationMapping",
    "Satellite",
    "TimestampedModel",
]
