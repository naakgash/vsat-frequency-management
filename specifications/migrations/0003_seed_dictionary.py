"""Seed the Specification Dictionary from the code registry.

The dictionary is structural rather than sample data: the application renders codes
through it on every page, so an empty dictionary is a broken install, not an empty one.
That is why this is a migration and not a management command.

Idempotent and **non-destructive**: it creates rows that are missing, and leaves existing
rows untouched. An administrator's edit to a description must survive the next deploy —
overwriting on every migrate would silently discard the work section 2 exists to enable.
"""

from django.db import migrations

from specifications.registry import CATEGORY_SEEDS, SPECIFICATION_SEEDS


def seed_dictionary(apps, schema_editor):
    Category = apps.get_model("specifications", "SpecificationCategory")
    Definition = apps.get_model("specifications", "SpecificationDefinition")

    categories = {}
    for code, name, description, order in CATEGORY_SEEDS:
        category, _ = Category.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": description, "display_order": order},
        )
        categories[code] = category

    for order, seed in enumerate(SPECIFICATION_SEEDS, start=1):
        Definition.objects.get_or_create(
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


def unseed_dictionary(apps, schema_editor):
    """Remove only the system-managed rows this migration created.

    Rows an administrator added by hand are left alone: a reversal should undo this
    migration, not clear the table.
    """
    Definition = apps.get_model("specifications", "SpecificationDefinition")
    Category = apps.get_model("specifications", "SpecificationCategory")

    Definition.objects.filter(code__in=[s.code for s in SPECIFICATION_SEEDS]).delete()
    Category.objects.filter(
        code__in=[code for code, _, _, _ in CATEGORY_SEEDS], specifications__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("specifications", "0002_code_immutable"),
    ]

    operations = [
        migrations.RunPython(seed_dictionary, unseed_dictionary),
    ]
