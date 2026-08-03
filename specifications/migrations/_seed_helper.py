"""Idempotent dictionary seeding usable outside a migration.

The migration seeds on a real deploy. Tests need the same rows without depending on
migrations having run, so both paths call this.
"""

from __future__ import annotations

from specifications.models import SpecificationCategory, SpecificationDefinition
from specifications.registry import CATEGORY_SEEDS, SPECIFICATION_SEEDS


def ensure_seeded() -> None:
    categories = {}
    for code, name, description, order in CATEGORY_SEEDS:
        category, _ = SpecificationCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": description, "display_order": order},
        )
        categories[code] = category

    for order, seed in enumerate(SPECIFICATION_SEEDS, start=1):
        SpecificationDefinition.objects.get_or_create(
            code=seed.code,
            defaults={
                "is_system_managed": True,
                "display_name": seed.display_name,
                "short_name": seed.short_name,
                "description": seed.description,
                "unit": seed.unit,
                "display_precision": seed.display_precision,
                "category": categories[seed.category],
                "data_type": seed.data_type,
                "direction_applicability": seed.direction,
                "is_calculated": seed.is_calculated,
                "calculation_note": seed.calculation_note,
                "visible_in_tables": seed.visible_in_tables,
                "visible_in_forms": seed.visible_in_forms,
                "visible_in_detail": seed.visible_in_detail,
                "display_order": order * 10,
            },
        )
