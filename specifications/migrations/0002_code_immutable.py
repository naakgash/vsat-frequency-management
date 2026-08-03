"""Prevent renaming a specification code that application logic refers to.

Specification section 2: *"The stable internal code must not be freely renamed after it
is used by application logic."* Design assumption A-20.

The form omits ``code`` from its fields and the service rejects it, so the application
cannot rename one. This trigger is the layer below that: a data migration, a management
shell, or a psql session would otherwise be able to rename a code and silently detach it
from the calculation engine, the column registry and the importer that refer to it by
name. The failure would surface much later as a missing value rather than as an error.

Same reasoning as the audit trigger in ``audit/0002``: where the consequence of a bypass
is silent and severe, the guarantee belongs in the database.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION specification_code_immutable() RETURNS trigger AS $$
BEGIN
    IF OLD.is_system_managed AND NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION
            'specification code % is referenced by application logic and cannot be renamed to %',
            OLD.code, NEW.code
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Edit the display name instead; the code is an internal identifier.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS specification_code_immutable();"

CREATE_TRIGGER = """
CREATE TRIGGER trg_specification_code_immutable
    BEFORE UPDATE ON specification_definition
    FOR EACH ROW EXECUTE FUNCTION specification_code_immutable();
"""

DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS trg_specification_code_immutable ON specification_definition;"
)


class Migration(migrations.Migration):
    dependencies = [
        ("specifications", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
