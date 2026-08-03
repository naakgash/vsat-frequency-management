"""Make audit_event append-only at the database level.

Specification section 18: "Audit records cannot be edited or deleted through the
application." Design assumption A-15 goes further and enforces it in the database,
because "through the application" is not a guarantee — ``queryset.update()``, a psql
session, or a well-intentioned maintenance script all bypass any Python-level guard.

A trigger is used rather than revoking UPDATE/DELETE from the application role, because
the on-premises deployment runs as a single database role that also needs DDL for
migrations. A trigger holds regardless of which role connects.
"""

from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_event is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'Audit history is immutable by design (specification section 18).';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS audit_event_immutable();"

CREATE_TRIGGER = """
CREATE TRIGGER trg_audit_event_immutable
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
"""

DROP_TRIGGER = "DROP TRIGGER IF EXISTS trg_audit_event_immutable ON audit_event;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTION, reverse_sql=DROP_FUNCTION),
        migrations.RunSQL(sql=CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
